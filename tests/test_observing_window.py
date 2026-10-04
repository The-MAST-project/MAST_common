"""`Site.observing_window()` defaults to the observing night, not the calendar date.

The window itself was always anchored correctly -- first sunset after 12:00 UTC, then
the next sunrise -- but the default `day` was `datetime.now(UTC).date()`, which stops
being the night in progress at 00:00 UTC, i.e. 02:00-03:00 local, mid-run. From there
until noon it named the *next* night, so the window came back starting some sixteen
hours out while the telescopes were observing (MAST_common#28).

Time is frozen by replacing the `datetime` name in `common.config.site` only, so no
patched clock escapes the call under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import common.config.site
from common.config.site import Location, Site
from common.mast_logging import observing_night, observing_night_date

# Neot Smadar. Any mid-latitude site shows the same split; these coordinates make the
# dusk and dawn times realistic for the local offset the docstrings quote.
LATITUDE, LONGITUDE, ELEVATION = 30.05, 35.02, 400.0


@pytest.fixture
def site() -> Site:
    return Site(
        name="test",
        project="mast",
        controller_host="controller",
        spec_host="spec",
        unit_ids="1-2",
        location=Location(latitude=LATITUDE, longitude=LONGITUDE, elevation=ELEVATION),
    )


class _FrozenMeta(type):
    """`site.py` also uses the patched name for `isinstance`, on datetimes astropy made.

    Those are plain `datetime`s, not instances of the subclass below, so without this
    the module's own `assert isinstance(window_start, datetime)` fails and the test
    reports a defect it invented itself.
    """

    def __instancecheck__(cls, obj):
        return isinstance(obj, datetime)


@pytest.fixture
def frozen_now(monkeypatch):
    """Pin `common.config.site`'s clock to an instant."""

    def _at(instant: datetime) -> datetime:
        class _Frozen(datetime, metaclass=_FrozenMeta):
            @classmethod
            def now(cls, tz=None):
                assert tz is not None, "the observing night must be taken from an aware UTC instant"
                return instant.astimezone(tz)

        monkeypatch.setattr(common.config.site, "datetime", _Frozen)
        return instant

    return _at


class TestObservingWindowDefaultDay:
    def test_after_utc_midnight_it_is_still_the_night_in_progress(self, site, frozen_now):
        """03:15 local, mid-observation. This is the case the calendar date got wrong."""
        now = frozen_now(datetime(2026, 8, 7, 0, 15, tzinfo=UTC))

        window = site.observing_window()

        assert window is not None
        assert window.start <= now <= window.end, (
            f"observing at {now:%H:%MZ} should fall inside the window, got {window.start}..{window.end}"
        )

    def test_before_utc_midnight_it_is_the_night_in_progress_too(self, site, frozen_now):
        """23:00 local, where the calendar date happened to agree already."""
        now = frozen_now(datetime(2026, 8, 6, 20, 0, tzinfo=UTC))

        window = site.observing_window()

        assert window is not None
        assert window.start <= now <= window.end

    def test_both_halves_of_one_night_give_the_same_window(self, site, frozen_now):
        evening = site.observing_window(day=observing_night_date(frozen_now(datetime(2026, 8, 6, 20, 0, tzinfo=UTC))))
        morning = site.observing_window(day=observing_night_date(frozen_now(datetime(2026, 8, 7, 0, 15, tzinfo=UTC))))

        assert evening is not None and morning is not None
        assert (evening.start, evening.end) == (morning.start, morning.end)

    def test_between_dawn_and_noon_utc_the_window_is_the_night_just_ended(self, site, frozen_now):
        """The contract callers must know: after dawn the default window is in the past.

        The night label turns at 12:00 UTC, so until then `observing_window()` still
        answers for the night that ended this morning. A caller wanting the *next*
        session has to ask for `observing_night_date(now) + 1 day` -- adding a day to
        the calendar date instead skips a night. MAST_gui's session tile does exactly
        that, and needs the follow-up.
        """
        now = frozen_now(datetime(2026, 8, 7, 9, 0, tzinfo=UTC))

        window = site.observing_window()
        next_window = site.observing_window(day=observing_night_date(now) + timedelta(days=1))

        assert window is not None and next_window is not None
        assert window.end < now, "the night that ended this morning is over"
        assert now < next_window.start, "the next night has not begun"


class TestObservingNightHelpers:
    def test_the_label_is_the_date_formatted(self):
        for instant in (
            datetime(2026, 8, 6, 11, 59, tzinfo=UTC),
            datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
            datetime(2026, 8, 7, 0, 15, tzinfo=UTC),
        ):
            assert observing_night(instant) == f"{observing_night_date(instant):%Y-%m-%d}"

    def test_the_date_turns_at_noon_utc(self):
        assert observing_night_date(datetime(2026, 8, 6, 11, 59, tzinfo=UTC)).isoformat() == "2026-08-05"
        assert observing_night_date(datetime(2026, 8, 6, 12, 0, tzinfo=UTC)).isoformat() == "2026-08-06"


# --------------------------------------------------------------- the IERS guard --
#
# `observing_window()` is the only frame transform in `common`, and astroplan is reached by
# every `import common.config`, so this one function is where an unapplied IERS policy would
# bite every MAST service. These tests pin the bug of 2026-10-01 (MAST_common#139), where the
# same ValueError stopped a mount, and the guard that now prevents it.
#
# Staleness is simulated with `auto_max_age = 1e-6`, which makes any prediction count as too
# old. No network, no cached table touched, so these pass on a machine whose table is current.


@pytest.fixture
def _restore_iers():
    """`iers.conf` is process-global; leaking it would make the suite order-dependent."""
    from astropy.utils import iers

    from common import iers_policy

    saved = {name: getattr(iers.conf, name) for name in iers.conf}
    saved_configured = iers_policy._configured
    yield
    for name, value in saved.items():
        setattr(iers.conf, name, value)
    iers_policy._configured = saved_configured


def test_an_aged_table_does_not_stop_the_observing_window(site, _restore_iers, caplog):
    """The regression. Before the guard this raised, and took a night's campaign with it."""
    from astropy.utils import iers

    from common import iers_policy

    iers_policy._configured = False  # as if no service had applied the policy
    iers.conf.auto_download = False
    iers.conf.auto_max_age = 1e-6
    iers.conf.iers_degraded_accuracy = "error"

    with caplog.at_level("WARNING"):
        window = site.observing_window()

    assert window is not None
    assert window.start < window.end
    # And it said so, rather than fixing it silently: a service that never calls
    # configure_astropy() must be discoverable from its log.
    assert "IERS policy was not applied" in caplog.text


def test_the_guard_is_quiet_when_the_application_already_applied_the_policy(site, _restore_iers, caplog):
    from common import iers_policy

    iers_policy._configured = False
    iers_policy.configure_astropy()

    with caplog.at_level("WARNING"):
        assert site.observing_window() is not None
    assert "IERS policy was not applied" not in caplog.text


def test_the_guard_does_not_load_a_table(site, _restore_iers, monkeypatch):
    """Cheap on purpose: asking for dusk must not parse ~20k rows or swap a global table.

    The guard's job is "nothing raises". Accuracy is the app lifespan's job, via
    `load_into_astropy()`, which costs ~510 ms and is called once.
    """
    from common import iers_policy

    def fail(*_args, **_kwargs):
        raise AssertionError("observing_window must not load an IERS table")

    monkeypatch.setattr(iers_policy, "load_into_astropy", fail)
    iers_policy._configured = False
    assert site.observing_window() is not None


def test_a_site_without_coordinates_needs_no_policy(_restore_iers, caplog):
    """The early return comes first, so no transform means no guard and no warning."""
    from common import iers_policy

    nowhere = Site(
        name="test",
        project="mast",
        controller_host="controller",
        spec_host="spec",
        unit_ids="1-2",
        location=Location(latitude=None, longitude=None, elevation=None),
    )
    iers_policy._configured = False
    with caplog.at_level("WARNING"):
        assert nowhere.observing_window() is None
    assert "IERS policy was not applied" not in caplog.text
