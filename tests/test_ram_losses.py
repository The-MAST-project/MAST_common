"""Products lost with the RAM disk are reported; claims on it are dropped (filing-areas 7.1).

When the RAM disk dies its contents are gone. `_drain_pending` used to drop a queued move
whose source had vanished without a word, and the folder reaper counted a vanished product
as moved. While RAM is demoted as GONE, a product no longer where it was written, and found
nowhere else, is reported -- once. A product that merely moved is not lost.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil

import pytest

import common.filer as filer_module
from common.filer import Filer, FilingArea, Location, MoveGuardian
from common.testing.filer import reset_filing_areas


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(filer_module, "_probe_cache", {})
    monkeypatch.setattr(filer_module, "_probe_threads", {})
    monkeypatch.setattr(Filer, "MONITOR_INTERVAL_SECONDS", 3600.0)
    monkeypatch.setattr(Filer, "_mover_pool", None)
    monkeypatch.setattr(Filer, "_ensure_sweeper", lambda self: None)
    monkeypatch.setattr(Filer, "_request_ramdisk_restart", lambda self: None)
    with Filer._pending_lock:
        Filer._pending.clear()
        Filer._in_flight.clear()
    guardian = MoveGuardian()
    for real in list(MoveGuardian._folder_claims):
        guardian._release_claim(real)
    with MoveGuardian._condition:
        MoveGuardian._protected.clear()
        MoveGuardian._moving.clear()
        MoveGuardian._products.clear()
    reset_filing_areas()
    yield
    for real in list(MoveGuardian._folder_claims):
        guardian._release_claim(real)
    reset_filing_areas()


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
        self.logger = logging.getLogger("test.ram-losses")

    monkeypatch.setattr(Filer, "__init__", fake_init)
    filer = Filer()
    filer.tmp = tmp_path  # type: ignore[attr-defined]
    return filer


def _frame(root: str, relative: str = "2026-10-21/Acquisitions/seq=0001/frame.fits") -> str:
    path = os.path.realpath(os.path.join(root, relative))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"SIMPLE")
    return path


def _ram_gone(unit):
    # The disk is gone: its root is missing, so a "not found" is the area's.
    shutil.rmtree(unit.tmp / "ram")
    assert unit.report_failure(FilingArea.RAM, unit.ram.root + "x", FileNotFoundError(errno.ENOENT, "x")) == "gone"


def _lost_errors(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and "lost with the RAM disk" in r.getMessage()
    ]


class TestTheQueue:
    def test_a_queued_product_gone_with_the_disk_is_reported_once(self, unit, caplog):
        caplog.set_level(logging.ERROR)
        frame = _frame(unit.ram.root)
        with Filer._pending_lock:
            Filer._pending[frame] = ""
        _ram_gone(unit)

        unit._drain_pending()
        with Filer._pending_lock:
            Filer._pending[frame] = ""  # queued again, somehow: still reported only once
        unit._drain_pending()

        errors = _lost_errors(caplog)
        assert len(errors) == 1 and "frame.fits" in errors[0]
        with Filer._pending_lock:
            assert frame not in Filer._pending

    def test_one_that_reached_the_share_is_not_lost(self, unit, caplog):
        caplog.set_level(logging.ERROR)
        frame = _frame(unit.ram.root)
        _frame(unit.shared.root)  # it got there before the disk died
        with Filer._pending_lock:
            Filer._pending[frame] = ""
        _ram_gone(unit)

        unit._drain_pending()

        assert _lost_errors(caplog) == []

    def test_nothing_is_called_lost_while_the_disk_is_merely_full(self, unit, caplog):
        caplog.set_level(logging.ERROR)
        frame = _frame(unit.ram.root)
        os.remove(frame)
        with Filer._pending_lock:
            Filer._pending[frame] = ""
        unit.report_failure(FilingArea.RAM, "x", OSError(errno.ENOSPC, "full"))

        unit._drain_pending()

        assert _lost_errors(caplog) == []


class TestTheReaper:
    def test_a_recorded_product_gone_with_the_disk_is_reported(self, unit):
        frame = _frame(unit.ram.root)
        folder = os.path.dirname(frame)
        with MoveGuardian().protect(frame):
            pass  # recorded as a product of the folder
        _ram_gone(unit)

        MoveGuardian()._reap_folder(os.path.realpath(folder), logger=None, timeout=0.2)

        assert frame in Filer._reported_lost

    def test_one_moved_to_the_share_is_not(self, unit):
        frame = _frame(unit.ram.root, "2026-10-21/Exposures/seq=0002/x.fits")
        folder = os.path.dirname(frame)
        with MoveGuardian().protect(frame):
            pass
        unit.move_ram_to_shared(frame)
        assert unit.flush(timeout=10)
        _ram_gone(unit)

        MoveGuardian()._reap_folder(os.path.realpath(folder), logger=None, timeout=0.2)

        assert frame not in Filer._reported_lost


class TestClaims:
    def test_the_claims_on_a_gone_ram_disk_are_dropped(self, unit):
        on_ram = os.path.dirname(_frame(unit.ram.root))
        elsewhere = os.path.dirname(_frame(unit.ram_fallback.root))
        guardian = MoveGuardian()
        assert guardian.claim_folder(on_ram) and guardian.claim_folder(elsewhere)

        _ram_gone(unit)

        assert os.path.realpath(on_ram) not in MoveGuardian._folder_claims
        assert os.path.realpath(elsewhere) in MoveGuardian._folder_claims, "only those under the dead root"

    def test_a_full_disk_keeps_its_claims(self, unit):
        on_ram = os.path.dirname(_frame(unit.ram.root))
        assert MoveGuardian().claim_folder(on_ram)

        unit.report_failure(FilingArea.RAM, "x", OSError(errno.ENOSPC, "full"))

        assert os.path.realpath(on_ram) in MoveGuardian._folder_claims
