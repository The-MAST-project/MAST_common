"""`PathMaker.make_observing_night_folder` labels an observing night, not a calendar day.

The folder holding a night's products used to be named from naive local time, so it
turned at local midnight -- 02:00-03:00 into the run -- and split every night across
two directories, while the logs for that same night (`observing_night`, MAST_common#29)
sat under a third name. These tests pin the turn at 12:00 UTC and pin the two to the
same label (MAST_common#28).

Time is frozen by replacing the `datetime` name in `common.paths` only, rather than
setting an attribute on the stdlib module, so nothing outside the call under test sees
a patched clock.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import common.paths
from common.mast_logging import observing_night
from common.paths import PathMaker


@pytest.fixture
def frozen(monkeypatch):
    """Pin `common.paths`'s clock to an instant, and return the folder it makes."""

    def _at(instant: datetime.datetime, root: Path) -> str:
        class _Frozen(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                assert tz is not None, "the night folder must be built from an aware UTC instant"
                return instant.astimezone(tz)

        monkeypatch.setattr(
            common.paths,
            "datetime",
            SimpleNamespace(datetime=_Frozen, UTC=datetime.UTC, timedelta=datetime.timedelta),
        )
        return Path(PathMaker.make_observing_night_folder(root=str(root))).name

    return _at


def _utc(year, month, day, hour, minute=0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.UTC)


class TestObservingNightFolder:
    def test_it_turns_at_noon_utc(self, frozen, tmp_path):
        """12:00 UTC is 14:00-15:00 local here -- daylight, with no run to interrupt."""
        assert frozen(_utc(2026, 8, 6, 11, 59), tmp_path) == "2026-08-05"
        assert frozen(_utc(2026, 8, 6, 12, 0), tmp_path) == "2026-08-06"

    def test_a_night_spanning_utc_midnight_stays_in_one_folder(self, frozen, tmp_path):
        """The old calendar-day naming broke exactly here, at 02:00-03:00 local."""
        evening = frozen(_utc(2026, 8, 6, 23, 59), tmp_path)
        after_midnight = frozen(_utc(2026, 8, 7, 0, 15), tmp_path)
        assert evening == after_midnight == "2026-08-06"

    def test_it_agrees_with_the_log_directory(self, frozen, tmp_path):
        """Products and logs from one night must be findable under one name."""
        for instant in (_utc(2026, 8, 6, 12, 0), _utc(2026, 8, 7, 0, 15), _utc(2026, 8, 7, 11, 59)):
            assert frozen(instant, tmp_path) == observing_night(instant)

    def test_it_creates_the_folder(self, frozen, tmp_path):
        assert (tmp_path / frozen(_utc(2026, 8, 6, 20, 0), tmp_path)).is_dir()
