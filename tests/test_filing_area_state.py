"""Filing-area state: demotion on a failed use, promotion by a probe (plans/filing-areas-design.md 5).

Every failure is simulated on temporary roots, never awaited: a removed root, a raised
`OSError` with the errno or winerror the real failure carries. The monitor's interval is
shortened so a promotion takes milliseconds.
"""

from __future__ import annotations

import errno
import logging
import os
import threading
import time

import pytest

import common.filer as filer_module
from common.filer import Filer, FilingArea, Location


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(filer_module, "_probe_cache", {})
    monkeypatch.setattr(filer_module, "_probe_threads", {})
    monkeypatch.setattr(Filer, "MONITOR_INTERVAL_SECONDS", 0.02)
    with Filer._state_lock:
        Filer._demoted.clear()
        Filer._promotion_hooks.clear()
    yield
    with Filer._state_lock:
        Filer._demoted.clear()
        Filer._promotion_hooks.clear()
        thread = Filer._monitor_thread
    if thread is not None:
        thread.join(2)


@pytest.fixture
def unit(tmp_path):
    """A unit's layout on temporary folders: RAM, the share and its fallbacks."""
    folders = {
        name: tmp_path / name
        for name in ("ram", "local", "local/ram-fallback", "local/share-fallback", "share", "share/mast00")
    }
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    filer = object.__new__(Filer)
    filer.ram = Location(str(folders["ram"]))
    filer.local = Location(str(folders["local"]))
    filer.ram_fallback = Location(str(folders["local/ram-fallback"]))
    filer.share_fallback = Location(str(folders["local/share-fallback"]))
    filer.share_root = Location(str(folders["share"]))
    filer.shared = Location(str(folders["share/mast00"]))
    filer.machine = filer.shared
    filer.logger = logging.getLogger("test.filing-area-state")
    filer.paths = folders  # type: ignore[attr-defined]
    return filer


def _oserror(code: int | None = None, winerror: int | None = None) -> OSError:
    exc = OSError(code, "simulated")
    if winerror is not None:
        exc.winerror = winerror  # type: ignore[attr-defined]
    return exc


def _warnings(caplog, area: FilingArea) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and f"area {area.name}" in r.getMessage()]


class TestDemotion:
    def test_on_the_primary_until_something_fails(self, unit):
        assert unit.current_root(FilingArea.RAM) == unit.ram.root
        assert unit.current_root(FilingArea.SHARE) == unit.shared.root
        assert unit.current_root(FilingArea.LOCAL) == unit.local.root

    def test_a_full_ram_disk_demotes_it_with_one_warning(self, unit, caplog):
        caplog.set_level(logging.INFO)
        full = _oserror(errno.ENOSPC)

        assert unit.report_failure(FilingArea.RAM, unit.ram.root + "a.fits", full) == "full"
        assert unit.report_failure(FilingArea.RAM, unit.ram.root + "b.fits", full) == "full"

        assert unit.current_root(FilingArea.RAM) == unit.ram_fallback.root
        assert len(_warnings(caplog, FilingArea.RAM)) == 1

    def test_the_demotion_holds_for_every_filer(self, unit):
        unit.report_failure(FilingArea.RAM, "x", _oserror(errno.ENOSPC))
        assert Filer().is_demoted(FilingArea.RAM)

    def test_a_path_level_error_does_not_demote(self, unit):
        assert unit.report_failure(FilingArea.RAM, unit.ram.root + "x", _oserror(errno.EACCES)) is None
        assert unit.report_failure(FilingArea.RAM, unit.ram.root + "x", _oserror(errno.EEXIST)) is None
        assert unit.current_root(FilingArea.RAM) == unit.ram.root

    def test_not_found_under_a_root_that_is_there_is_about_the_path(self, unit):
        missing = unit.ram.root + "no/such/sub/folder"
        assert unit.report_failure(FilingArea.RAM, missing, FileNotFoundError(errno.ENOENT, "x")) is None

    def test_not_found_with_the_root_itself_gone_is_the_ram_disk_gone(self, unit):
        unit.paths["ram"].rmdir()
        assert unit.report_failure(FilingArea.RAM, unit.ram.root + "x", FileNotFoundError(errno.ENOENT, "x")) == "gone"

    @pytest.mark.parametrize("winerror", [53, 59, 64, 67, 1231])
    def test_a_network_path_gone_makes_the_share_unreachable(self, unit, winerror):
        assert unit.report_failure(FilingArea.SHARE, "x", _oserror(winerror=winerror)) == "unreachable"
        assert unit.current_root(FilingArea.SHARE) == unit.share_fallback.root

    def test_a_device_not_ready_is_gone(self, unit):
        assert unit.report_failure(FilingArea.RAM, "x", _oserror(winerror=21)) == "gone"

    def test_a_timeout_is_unreachable(self, unit):
        assert unit.report_failure(FilingArea.SHARE, "x", TimeoutError("probe")) == "unreachable"

    def test_local_is_never_demoted(self, unit):
        assert unit.report_failure(FilingArea.LOCAL, "x", _oserror(errno.ENOSPC)) is None

    def test_an_area_without_a_fallback_is_never_demoted(self, unit):
        unit.share_fallback = None  # as on Linux
        assert unit.report_failure(FilingArea.SHARE, "x", _oserror(winerror=53)) is None
        assert unit.current_root(FilingArea.SHARE) == unit.shared.root


class TestPromotion:
    def test_check_now_promotes_a_writable_primary_and_runs_the_hooks(self, unit, caplog):
        caplog.set_level(logging.INFO)
        promoted: list[FilingArea] = []
        Filer.on_promotion(promoted.append)
        unit.report_failure(FilingArea.RAM, "x", _oserror(errno.ENOSPC))

        assert unit.check_now(FilingArea.RAM) is True

        assert unit.current_root(FilingArea.RAM) == unit.ram.root
        assert promoted == [FilingArea.RAM]
        assert any("writable again" in r.getMessage() for r in caplog.records if r.levelno == logging.INFO)

    def test_check_now_leaves_an_unwritable_primary_demoted(self, unit):
        unit.paths["ram"].rmdir()
        unit.report_failure(FilingArea.RAM, "x", FileNotFoundError(errno.ENOENT, "x"))

        assert unit.check_now(FilingArea.RAM) is False
        assert unit.current_root(FilingArea.RAM) == unit.ram_fallback.root

    def test_the_share_comes_back_when_the_share_does_not_this_machines_folder(self, unit):
        # A new machine's folder does not exist until something creates it (4.3).
        unit.report_failure(FilingArea.SHARE, "x", _oserror(winerror=53))
        unit.paths["share/mast00"].rmdir()

        assert unit.check_now(FilingArea.SHARE) is True

    def test_the_monitor_promotes_on_its_own_and_then_stops(self, unit, monkeypatch):
        # The monitor builds its own Filer(); make that one this layout.
        monkeypatch.setattr(Filer, "__init__", lambda self, logger=None: self.__dict__.update(unit.__dict__))
        done = threading.Event()
        Filer.on_promotion(lambda area: done.set())
        unit.paths["share"].rename(unit.paths["share"].with_name("share-away"))
        unit.report_failure(FilingArea.SHARE, "x", _oserror(winerror=64))

        time.sleep(0.1)
        assert unit.is_demoted(FilingArea.SHARE), "nothing to promote to while the share is away"
        unit.paths["share"].with_name("share-away").rename(unit.paths["share"])

        assert done.wait(2), "the monitor should have promoted SHARE"
        assert not unit.is_demoted(FilingArea.SHARE)
        deadline = time.monotonic() + 2
        while Filer._monitor_thread is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert Filer._monitor_thread is None, "with nothing demoted, no monitor thread runs"

    def test_a_failing_hook_does_not_stop_the_others(self, unit):
        called: list[str] = []

        def bad(area):
            raise RuntimeError("boom")

        Filer.on_promotion(bad)
        Filer.on_promotion(lambda area: called.append("good"))
        unit.report_failure(FilingArea.RAM, "x", _oserror(errno.ENOSPC))

        assert unit.check_now(FilingArea.RAM)
        assert called == ["good"]


class TestFallbacksAreFilingAreas:
    def test_a_path_on_a_fallback_is_in_its_area(self, unit):
        path = unit.ram_fallback.root + "2026-10-21/Exposures/seq=0001"
        assert unit.area_of(path) == (FilingArea.RAM, "2026-10-21/Exposures/seq=0001")
        assert unit.to_top(FilingArea.SHARE, path) == unit.shared.root + "2026-10-21/Exposures/seq=0001"

    def test_the_fallback_wins_over_local_that_contains_it(self, unit):
        # C:/MAST/ram-fallback/ is inside C:/MAST/: the longest root decides.
        assert unit.area_of(unit.share_fallback.root + "x")[0] is FilingArea.SHARE
        assert unit.area_of(unit.local.root + "x")[0] is FilingArea.LOCAL

    def test_locate_looks_on_the_share_fallback_before_the_ram_disk(self, unit):
        for root in (unit.share_fallback.root, unit.ram.root):
            os.makedirs(f"{root}n/seq=0001")
        assert unit.locate(unit.ram.root + "n/seq=0001") == unit.share_fallback.root + "n/seq=0001"
