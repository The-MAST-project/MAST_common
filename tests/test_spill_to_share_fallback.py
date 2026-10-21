"""RAM products go to the share fallback when the share is unreachable (filing-areas plan 6.1).

The destination is decided when a product moves, by creating its folder; a share found
unreachable is demoted and the product spills to `C:/MAST/share-fallback/` at once, rather
than waiting on the RAM disk, which a long outage would fill. Without a share fallback
(Linux; Filers built without one) nothing changes: the product stays queued.
"""

from __future__ import annotations

import errno
import logging
import os

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
    with Filer._pending_lock:
        Filer._pending.clear()
        Filer._in_flight.clear()
    reset_filing_areas()
    with MoveGuardian._condition:
        MoveGuardian._protected.clear()
        MoveGuardian._moving.clear()
        MoveGuardian._products.clear()
    yield
    reset_filing_areas()


@pytest.fixture
def unit(tmp_path, monkeypatch):
    names = ("ram", "local/ram-fallback", "local/share-fallback", "share/mast00")
    for name in names:
        (tmp_path / name).mkdir(parents=True)

    def fake_init(self, logger=None):
        self.ram = Location(str(tmp_path / "ram"))
        self.local = Location(str(tmp_path / "local"))
        self.ram_fallback = Location(str(tmp_path / "local/ram-fallback"))
        self.share_fallback = Location(str(tmp_path / "local/share-fallback"))
        self.share_root = Location(str(tmp_path / "share"))
        self.shared = Location(str(tmp_path / "share/mast00"))
        self.logger = logging.getLogger("test.spill")

    monkeypatch.setattr(Filer, "__init__", fake_init)
    filer = Filer()
    filer.tmp = tmp_path  # type: ignore[attr-defined]
    return filer


def _product(root: str, relative: str = "2026-10-21/Acquisitions/seq=0001/frame.fits") -> str:
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"SIMPLE  =  T")
    return path


def _share_away(unit):
    (unit.tmp / "share").rename(unit.tmp / "share-away")


REL = "2026-10-21/Acquisitions/seq=0001/frame.fits"


class TestShareUp:
    def test_a_product_goes_to_the_share(self, unit):
        src = _product(unit.ram.root)

        unit.move_ram_to_shared(src)
        assert unit.flush(timeout=10)

        assert os.path.isfile(unit.shared.root + REL)
        assert not os.path.exists(src)
        assert not unit.is_demoted(FilingArea.SHARE)


class TestShareUnreachable:
    def test_the_share_is_demoted_once_and_the_product_spills_to_its_fallback(self, unit, caplog):
        caplog.set_level(logging.WARNING)
        _share_away(unit)
        first, second = _product(unit.ram.root), _product(unit.ram.root, "2026-10-21/Exposures/x.fits")

        unit.move_ram_to_shared([first, second])
        assert unit.flush(timeout=10), "spilled, not left queued"

        assert os.path.isfile(unit.share_fallback.root + REL)
        assert os.path.isfile(unit.share_fallback.root + "2026-10-21/Exposures/x.fits")
        assert not os.path.exists(first)
        assert unit.is_demoted(FilingArea.SHARE)
        assert len([r for r in caplog.records if "area SHARE" in r.getMessage()]) == 1

    def test_while_demoted_the_share_is_not_probed(self, unit, monkeypatch):
        unit.report_failure(FilingArea.SHARE, "x", _net_error())
        monkeypatch.setattr(Filer, "ensure_shared_root", lambda self: pytest.fail("probed the share while demoted"))
        src = _product(unit.ram.root)

        unit.move_ram_to_shared(src)
        assert unit.flush(timeout=10)

        assert os.path.isfile(unit.share_fallback.root + REL)

    def test_a_failed_folder_creation_on_the_share_demotes_it_and_uses_the_fallback(self, unit, monkeypatch):
        real = os.makedirs
        share = unit.share_root.root.rstrip("/")

        def makedirs(path, *a, **k):
            if str(path).replace("\\", "/").startswith(share):
                raise _net_error()
            return real(path, *a, **k)

        monkeypatch.setattr(filer_module.os, "makedirs", makedirs)
        src = _product(unit.ram.root)

        unit.move_ram_to_shared(src)
        assert unit.flush(timeout=10)

        assert unit.is_demoted(FilingArea.SHARE)
        assert os.path.isfile(unit.share_fallback.root + REL)

    def test_a_path_level_failure_leaves_it_queued_on_the_ram_disk(self, unit, monkeypatch):
        real = os.makedirs
        share = unit.share_root.root.rstrip("/")

        def makedirs(path, *a, **k):
            if str(path).replace("\\", "/").startswith(share):
                raise PermissionError(errno.EACCES, "denied")
            return real(path, *a, **k)

        monkeypatch.setattr(filer_module.os, "makedirs", makedirs)
        src = _product(unit.ram.root)

        unit.move_ram_to_shared(src)
        unit.flush(timeout=0.5)

        assert os.path.isfile(src), "one bad path must not send products elsewhere"
        assert not unit.is_demoted(FilingArea.SHARE)
        with Filer._pending_lock:
            assert os.path.realpath(src) in Filer._pending


class TestTheDestinationIsDecidedWhenItMoves:
    def test_queued_while_the_share_was_up_drained_after_it_went(self, unit):
        src = _product(unit.ram.root)
        real = os.path.realpath(src)
        with Filer._pending_lock:
            Filer._pending[real] = unit.shared.root + REL  # queued for the share
        _share_away(unit)

        unit._drain_pending()

        assert os.path.isfile(unit.share_fallback.root + REL)
        with Filer._pending_lock:
            assert real not in Filer._pending


class TestTheRamFallback:
    def test_a_product_on_the_ram_fallback_goes_to_the_share(self, unit):
        src = _product(unit.ram_fallback.root)

        unit.move_ram_to_shared(src)
        assert unit.flush(timeout=10)

        assert os.path.isfile(unit.shared.root + REL)
        assert not os.path.exists(src)


class TestWithoutAShareFallback:
    def test_it_stays_queued_as_before(self, unit):
        unit.share_fallback = None
        _share_away(unit)
        src = _product(unit.ram.root)

        unit.move_ram_to_shared(src)

        assert os.path.isfile(src)
        assert not unit.is_demoted(FilingArea.SHARE)
        with Filer._pending_lock:
            assert os.path.realpath(src) in Filer._pending


def _net_error() -> OSError:
    exc = OSError(None, "The network path was not found")
    exc.winerror = 53  # type: ignore[attr-defined]
    return exc
