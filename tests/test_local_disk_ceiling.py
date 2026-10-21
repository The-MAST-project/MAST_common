"""The local disk's ceiling: at or above 90% used, nothing more goes to the fallbacks.

plans/filing-areas-design.md 6.4 (decided 2026-10-14): a full system disk is worse than lost
data, since Windows itself degrades. At the ceiling, products stay on the RAM disk; draining,
which frees the disk, goes on. The disk's reading is set, never the runner's real one.
"""

from __future__ import annotations

import logging
import os

import pytest

import common.filer as filer_module
from common.filer import Filer, FilingArea, Location, MoveGuardian
from common.testing.filer import DiskUsage, reset_filing_areas

REL = "2026-10-21/Acquisitions/seq=0001/frame.fits"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(filer_module, "_probe_cache", {})
    monkeypatch.setattr(filer_module, "_probe_threads", {})
    monkeypatch.setattr(Filer, "MONITOR_INTERVAL_SECONDS", 3600.0)
    monkeypatch.setattr(Filer, "_mover_pool", None)
    monkeypatch.setattr(Filer, "_ensure_sweeper", lambda self: None)
    with Filer._pending_lock:
        Filer._pending.clear()
        Filer._in_flight.clear()
    with MoveGuardian._condition:
        MoveGuardian._protected.clear()
        MoveGuardian._moving.clear()
        MoveGuardian._products.clear()
    reset_filing_areas()
    yield
    reset_filing_areas()


@pytest.fixture
def used(monkeypatch):
    """Set the local disk's usage, as a fraction; the cache is bypassed by moving the clock."""
    clock = [1000.0]
    monkeypatch.setattr(filer_module, "_probe_clock", lambda: clock[0])

    def set_used(fraction: float) -> None:
        clock[0] += Filer.LOCAL_DISK_CACHE_SECONDS + 1
        monkeypatch.setattr(
            filer_module, "_disk_usage", lambda path: DiskUsage(total=1000, used=int(fraction * 1000), free=0)
        )

    set_used(0.5)
    return set_used


@pytest.fixture
def unit(tmp_path, monkeypatch):
    for name in ("ram", "local/ram-fallback", "local/share-fallback", "share/mast00"):
        (tmp_path / name).mkdir(parents=True)

    def fake_init(self, logger=None):
        self.ram = Location(str(tmp_path / "ram"))
        self.local = Location(str(tmp_path / "local"))
        self.ram_fallback = Location(str(tmp_path / "local/ram-fallback"))
        self.share_fallback = Location(str(tmp_path / "local/share-fallback"))
        self.share_root = Location(str(tmp_path / "share"))
        self.shared = Location(str(tmp_path / "share/mast00"))
        self.logger = logging.getLogger("test.ceiling")

    monkeypatch.setattr(Filer, "__init__", fake_init)
    return Filer()


def _net_error() -> OSError:
    exc = OSError(None, "The network path was not found")
    exc.winerror = 53  # type: ignore[attr-defined]
    return exc


def _product(root: str, relative: str = REL) -> str:
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"SIMPLE")
    return path


def _messages(caplog, level) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level and "local disk" in r.getMessage()]


class TestTheCeiling:
    def test_below_it_the_share_fallback_takes_products(self, unit, used):
        used(0.89)
        unit.report_failure(FilingArea.SHARE, "x", _net_error())
        src = _product(unit.ram.root)

        unit.move_ram_to_shared(src)
        assert unit.flush(timeout=10)

        assert os.path.isfile(unit.share_fallback.root + REL)

    def test_at_it_products_stay_on_the_ram_disk(self, unit, used, caplog):
        caplog.set_level(logging.INFO)
        used(0.90)
        unit.report_failure(FilingArea.SHARE, "x", _net_error())
        first, second = _product(unit.ram.root), _product(unit.ram.root, "2026-10-21/x.fits")

        unit.move_ram_to_shared([first, second])
        unit.flush(timeout=0.5)

        assert os.path.isfile(first) and os.path.isfile(second)
        assert not os.path.exists(unit.share_fallback.root + REL)
        with Filer._pending_lock:
            assert os.path.realpath(first) in Filer._pending, "still queued, for when it frees"
        assert len(_messages(caplog, logging.ERROR)) == 1, "one ERROR for the ceiling, not one per product"

    def test_a_demoted_area_has_nowhere_to_write_at_the_ceiling(self, unit, used):
        used(0.95)
        unit.report_failure(FilingArea.RAM, "x", OSError(28, "No space left on device"))
        unit.report_failure(FilingArea.SHARE, "x", _net_error())

        assert unit.current_root(FilingArea.RAM) is None
        assert unit.current_root(FilingArea.SHARE) is None

    def test_primaries_and_the_local_disk_are_unaffected(self, unit, used):
        used(0.99)
        assert unit.current_root(FilingArea.RAM) == unit.ram.root
        assert unit.current_root(FilingArea.SHARE) == unit.shared.root
        assert unit.current_root(FilingArea.LOCAL) == unit.local.root

    def test_back_below_it_says_so_once_and_the_fallback_takes_products_again(self, unit, used, caplog):
        caplog.set_level(logging.INFO)
        unit.report_failure(FilingArea.SHARE, "x", _net_error())
        used(0.92)
        assert unit.current_root(FilingArea.SHARE) is None

        used(0.80)
        assert unit.current_root(FilingArea.SHARE) == unit.share_fallback.root
        assert unit.current_root(FilingArea.SHARE) == unit.share_fallback.root

        assert len(_messages(caplog, logging.ERROR)) == 1
        assert len([m for m in _messages(caplog, logging.INFO) if "below its ceiling" in m]) == 1


class TestDrainingIsNeverRefused:
    def test_the_share_fallback_still_drains_to_the_share(self, unit, used):
        used(0.97)
        _product(unit.share_fallback.root)

        assert unit.drain_fallbacks()[1] == 1
        assert os.path.isfile(unit.shared.root + REL)


class TestTheReading:
    def test_a_reading_that_fails_counts_as_not_full(self, unit, used, monkeypatch, caplog):
        caplog.set_level(logging.ERROR)

        def broken(path):
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(filer_module, "_disk_usage", broken)
        Filer._local_usage = None

        assert unit.local_disk_full() is False
        assert any("cannot read the local disk" in m for m in [r.getMessage() for r in caplog.records])

    def test_the_reading_is_cached(self, unit, used, monkeypatch):
        calls = []

        def counting(path):
            calls.append(path)
            return DiskUsage(total=100, used=10, free=90)

        Filer._local_usage = None
        monkeypatch.setattr(filer_module, "_disk_usage", counting)
        for _ in range(5):
            unit.local_disk_full()

        assert len(calls) == 1
