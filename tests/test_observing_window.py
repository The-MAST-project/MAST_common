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
