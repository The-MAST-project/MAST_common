"""`common.iers_policy`: the policy, and the table cache it loads from.

The staleness fixture here is the one that matters. `iers.conf.auto_max_age = 1e-6` makes any
prediction count as too old, which reproduces the 2026-10-01 failure exactly -- with no
network, and without touching any cached table. That is how the regression tests below can
assert "a transform does not raise" on a machine whose table happens to be current.

`iers.conf` is process-global, so every test that touches it restores it. Without that, one
test's policy leaks into the next and the suite's results depend on collection order.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from astropy.utils import iers

from common import iers_policy

# A real `finals2000A.all` is ~3.5 MB and the fixtures here only need `write()`/`newest()`
# to accept and find bytes; `read_predictive_mjd` is exercised separately against a table
# astropy can actually parse.
CONTENT = b"not a real IERS table, and nothing in this test parses it\n"


@pytest.fixture(autouse=True)
def _restore_iers_conf():
    """Save and restore every `iers.conf` key this module can touch, plus module state."""
    saved = {name: getattr(iers.conf, name) for name in iers.conf}
    saved_configured = iers_policy._configured
    saved_warning = iers_policy._last_lag_warning
    yield
    for name, value in saved.items():
        setattr(iers.conf, name, value)
    iers_policy._configured = saved_configured
    iers_policy._last_lag_warning = saved_warning


@pytest.fixture
def cache(tmp_path):
    return str(tmp_path / "iers-cache")


def _stamped(when: datetime, predictive_mjd: float) -> str:
    return f"{iers_policy.PREFIX}{iers_policy._stamp(when)}-pm{predictive_mjd:.0f}{iers_policy.SUFFIX}"


# ----------------------------------------------------------------- the policy --


def test_the_policy_is_applied_and_is_idempotent():
    iers_policy._configured = False
    assert iers_policy.configure_astropy() is True
    assert iers.conf.auto_download is False
    assert iers.conf.auto_max_age is None
    assert iers.conf.iers_degraded_accuracy == "warn"
    assert iers_policy.is_configured()

    # The second call must be a no-op: the service entry point and `common`'s own defensive
    # call must not fight each other.
    assert iers_policy.configure_astropy() is False
    assert iers_policy.configure_astropy(force=True) is True


def test_an_unknown_conf_key_is_reported_and_skipped(monkeypatch, caplog):
    """`iers.conf` silently accepts unknown attributes, so the guard must be explicit.

    Verified against astropy 8.0.1: `iers.conf.whatever = 1` neither raises nor takes effect.
    A bare assignment against an astropy that renamed one of these keys would therefore read
    as applied while leaving the default in force. MAST_spec runs astropy 7, MAST_unit 8.
    """
    monkeypatch.setitem(iers_policy.POLICY, "no_such_setting", "x")
    iers_policy._configured = False
    with caplog.at_level("WARNING"):
        iers_policy.configure_astropy()
    assert "no_such_setting" in caplog.text
    # The real keys still got set -- one unknown name must not abort the rest.
    assert iers.conf.auto_download is False


def test_download_can_be_re_enabled_by_environment(monkeypatch):
    monkeypatch.setenv(iers_policy.ALLOW_DOWNLOAD_ENV, "1")
    iers_policy._configured = False
    iers_policy.configure_astropy()
    assert iers.conf.auto_download is True


@pytest.mark.parametrize("value", ["", "0", "no", "off", "false"])
def test_download_stays_off_for_anything_but_an_affirmative(monkeypatch, value):
    monkeypatch.setenv(iers_policy.ALLOW_DOWNLOAD_ENV, value)
    iers_policy._configured = False
    iers_policy.configure_astropy()
    assert iers.conf.auto_download is False


# ------------------------------------------------- what the policy is FOR --


def _a_transform():
    """One alt/az -> ICRS transform at "now", the shape every MAST pointing path uses."""
    import astropy.units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time

    location = EarthLocation(lat=30.053 * u.deg, lon=35.041 * u.deg, height=400 * u.m)
    frame = AltAz(alt=35 * u.deg, az=18 * u.deg, obstime=Time(datetime.now(UTC)), location=location)
    return SkyCoord(frame).icrs


def test_without_the_policy_a_stale_table_raises():
    """Pins the hazard, and pins WHICH setting causes it.

    `auto_max_age = 1e-6` makes any prediction too old, which is how a stale table is
    simulated on a machine whose table is current -- no network, no cache tampering. Note
    `iers_degraded_accuracy` is left at "error" here only for realism: this raise comes from
    `IERS_Auto._check_interpolate_indices`, which does not consult it.
    """
    import warnings

    iers.conf.auto_download = False
    iers.conf.auto_max_age = 1e-6
    iers.conf.iers_degraded_accuracy = "error"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(ValueError, match="predictive values"):
            _a_transform()


def test_the_policy_prevents_that_raise():
    """The 2026-10-01 regression: identical inputs, policy applied, must not raise.

    Deliberately does NOT re-set `auto_max_age` afterwards. `auto_max_age = None` IS the
    mechanism -- astropy turns it into `np.finfo(float).max` so the staleness comparison can
    never be true -- so overriding it after applying the policy would test nothing except
    that the override works. Paired with the test above, which shows the same transform
    raising when the policy is absent.
    """
    import warnings

    iers.conf.auto_max_age = 1e-6  # start from the hazard, to prove the policy clears it
    iers_policy._configured = False
    iers_policy.configure_astropy()
    assert iers.conf.auto_max_age is None

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # fatal warnings too: it must not even warn here
        coordinates = _a_transform()
    assert -90.0 <= coordinates.dec.deg <= 90.0


def test_iers_degraded_accuracy_still_defaults_to_raising():
    """The second mechanism, and why `"warn"` is in the policy at all.

    `IERS._check_interpolate_indices` raises `IERSRangeError` for times outside the table
    entirely -- a different code path from `auto_max_age`, and one that matters more with
    downloads off, since a cached table eventually ages past the ~1 year of predictions it
    carries. If a future astropy changes this default, this test fails and the policy can be
    revisited rather than carried on an assumption.
    """
    assert iers.conf.iers_degraded_accuracy == "error", "astropy's default changed; re-read the policy's rationale"


# ------------------------------------------------------------------ the cache --


def test_write_then_find_round_trips(cache):
    when = datetime(2026, 10, 4, 9, 30, tzinfo=UTC)
    path = iers_policy.write(cache, CONTENT, 61315.0, when=when)
    assert path is not None

    found = iers_policy.newest(cache)
    assert found is not None
    found_path, fetched_at, predictive_mjd = found
    assert found_path == path
    assert fetched_at == when
    assert predictive_mjd == 61315.0
    # Read back from the name, not by parsing 20k rows.
    assert iers_policy.parse_name(os.path.basename(path)) == (when, 61315.0)


def test_a_candidate_that_does_not_advance_is_refused(cache):
    """The 2026-09-30 case: a fetch succeeded and left the cache no fresher.

    With a single source (MAST_common#139) this refusal is the only thing that distinguishes
    "we downloaded" from "we are more current than we were".
    """
    first = iers_policy.write(cache, CONTENT, 61315.0, when=datetime(2026, 10, 4, tzinfo=UTC))
    assert first is not None

    for candidate in (61252.0, 61315.0):  # older, and identical
        assert iers_policy.write(cache, b"newer bytes", candidate, when=datetime(2026, 10, 5, tzinfo=UTC)) is None

    found = iers_policy.newest(cache)
    assert found is not None and found[2] == 61315.0
    assert len(iers_policy.cache_files(cache)) == 1


def test_an_advancing_candidate_is_stored_and_becomes_newest(cache):
    iers_policy.write(cache, CONTENT, 61252.0, when=datetime(2026, 9, 30, tzinfo=UTC))
    iers_policy.write(cache, CONTENT, 61315.0, when=datetime(2026, 10, 4, tzinfo=UTC))
    found = iers_policy.newest(cache)
    assert found is not None and found[2] == 61315.0
    assert len(iers_policy.cache_files(cache)) == 2


def test_lag_is_measured_from_the_last_measured_value_not_the_fetch(cache):
    """A fetch an hour ago that delivered a two-month-old table must read as stale."""
    fetched_just_now = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
    iers_policy.write(cache, CONTENT, 61252.0, when=fetched_just_now)  # 2026-07-31

    lag = iers_policy.lag_days(cache, when=fetched_just_now)
    assert lag is not None
    assert 64.0 < lag < 66.0
    assert iers_policy.is_stale(cache, when=fetched_just_now)


def test_a_current_table_is_not_stale(cache):
    when = datetime(2026, 10, 4, tzinfo=UTC)
    iers_policy.write(cache, CONTENT, iers_policy.now_mjd(when) - 2.0, when=when)
    assert iers_policy.is_stale(cache, when=when) is False


def test_an_absent_cache_is_stale_and_has_no_lag(cache):
    assert iers_policy.lag_days(cache) is None
    assert iers_policy.is_stale(cache) is True
    assert iers_policy.newest(cache) is None
    assert iers_policy.cache_files(cache) == []


def test_pruning_keeps_the_newest_copies(cache):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for day in range(iers_policy.KEEP_COPIES + 3):
        iers_policy.write(cache, CONTENT, 60000.0 + day, when=base + timedelta(days=day))
    names = iers_policy.cache_files(cache)
    assert len(names) == iers_policy.KEEP_COPIES
    # The survivors are the newest, and `latest` is not mistaken for one of them.
    assert names[-1] == _stamped(base + timedelta(days=iers_policy.KEEP_COPIES + 2), 60000.0 + iers_policy.KEEP_COPIES + 2)
    assert iers_policy.LATEST not in names


def test_an_interrupted_write_is_neither_loaded_nor_pruned(cache):
    os.makedirs(cache, exist_ok=True)
    leftover = os.path.join(cache, f"{iers_policy.PREFIX}20260101T000000Z-pm60000{iers_policy.SUFFIX}.tmp")
    with open(leftover, "wb") as fp:
        fp.write(b"half a download")

    assert iers_policy.cache_files(cache) == []
    iers_policy.write(cache, CONTENT, 61315.0)
    assert os.path.exists(leftover), "a .tmp from another process must survive our prune"


def test_an_unwritable_cache_directory_is_survivable(cache, monkeypatch):
    """A full disk or a read-only directory is not a reason to stop a telescope."""

    def boom(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(iers_policy.os, "makedirs", boom)
    assert iers_policy.write(cache, CONTENT, 61315.0) is None


def test_garbage_in_the_cache_directory_is_ignored(cache):
    os.makedirs(cache, exist_ok=True)
    for name in ("finals2000A.all", "iers-finals2000A-nonsense.all", "notes.txt", "latest"):
        with open(os.path.join(cache, name), "wb") as fp:
            fp.write(b"x")
    assert iers_policy.cache_files(cache) == []
    assert iers_policy.parse_name("iers-finals2000A-nonsense.all") is None


# ------------------------------------------------------------ loading astropy --


def test_loading_without_a_cache_reports_false(cache, caplog):
    with caplog.at_level("WARNING"):
        assert iers_policy.load_into_astropy(cache) is False
    assert "no cached table" in caplog.text


def test_loading_an_unparseable_table_reports_false_and_does_not_raise(cache, caplog):
    iers_policy.write(cache, CONTENT, 61315.0)
    with caplog.at_level("WARNING"):
        assert iers_policy.load_into_astropy(cache) is False
    assert "could not load" in caplog.text


def test_read_predictive_mjd_rejects_what_is_not_a_table(tmp_path, caplog):
    path = tmp_path / "junk.all"
    path.write_bytes(b"<html>captive portal</html>")
    with caplog.at_level("WARNING"):
        assert iers_policy.read_predictive_mjd(str(path)) is None


# ---------------------------------------------------------------- the warning --


def test_a_stale_table_warns_once_per_interval(cache, caplog):
    when = datetime(2026, 10, 4, tzinfo=UTC)
    iers_policy.write(cache, CONTENT, 61252.0, when=when)

    iers_policy._last_lag_warning = None
    with caplog.at_level("WARNING"):
        iers_policy.log_lag(cache, when=when)
        iers_policy.log_lag(cache, when=when + timedelta(hours=1))
    assert caplog.text.count("days behind") == 1, "a warning every 30 minutes trains people to ignore it"

    with caplog.at_level("WARNING"):
        iers_policy.log_lag(cache, when=when + timedelta(days=2))
    assert caplog.text.count("days behind") == 2


def test_a_fresh_table_does_not_warn(cache, caplog):
    when = datetime(2026, 10, 4, tzinfo=UTC)
    iers_policy.write(cache, CONTENT, iers_policy.now_mjd(when) - 2.0, when=when)
    iers_policy._last_lag_warning = None
    with caplog.at_level("WARNING"):
        iers_policy.log_lag(cache, when=when)
    assert "days behind" not in caplog.text


def test_the_warning_is_ascii(cache, caplog):
    """MAST_common#63: the daily log file's consumers are not all safe with more."""
    when = datetime(2026, 10, 4, tzinfo=UTC)
    iers_policy.write(cache, CONTENT, 61252.0, when=when)
    iers_policy._last_lag_warning = None
    with caplog.at_level("WARNING"):
        iers_policy.log_lag(cache, when=when)
    caplog.text.encode("ascii")  # raises UnicodeEncodeError if anything crept in
