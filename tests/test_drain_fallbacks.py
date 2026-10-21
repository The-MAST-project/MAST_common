"""Draining the fallbacks back towards the share (plans/filing-areas-design.md 6.3).

What spilled to the share fallback while the share was down goes to the share once it is
back -- at once, on promotion -- and what the RAM fallback holds goes the way any RAM product
goes. Also on each sweeper pass and at start-up.
"""

from __future__ import annotations

import logging
import os
import threading

import pytest

import common.filer as filer_module
from common.filer import Filer, FilingArea, Location, MoveGuardian
from common.testing.filer import reset_filing_areas

REL = "2026-10-21/Acquisitions/seq=0001"


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
    guardian = MoveGuardian()
    for real in list(MoveGuardian._folder_claims):
        guardian._release_claim(real)
    with MoveGuardian._condition:
        MoveGuardian._protected.clear()
        MoveGuardian._moving.clear()
        MoveGuardian._products.clear()
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
        self.logger = logging.getLogger("test.drain")

    monkeypatch.setattr(Filer, "__init__", fake_init)
    return Filer()


def _products(root: str, relative: str = REL, names=("a.fits", "b.fits")) -> str:
    folder = os.path.join(root, relative)
    os.makedirs(folder, exist_ok=True)
    for name in names:
        with open(os.path.join(folder, name), "wb") as f:
            f.write(name.encode())
    return folder


def _net_error() -> OSError:
    exc = OSError(None, "The network path was not found")
    exc.winerror = 53  # type: ignore[attr-defined]
    return exc


class TestTheShareFallback:
    def test_drained_to_the_share_and_emptied(self, unit):
        _products(unit.share_fallback.root)

        assert unit.drain_fallbacks() == (0, 1)

        assert sorted(os.listdir(unit.shared.root + REL)) == ["a.fits", "b.fits"]
        assert os.listdir(unit.share_fallback.root) == [], "empty folders are removed, the root is kept"

    def test_not_while_the_share_is_demoted(self, unit):
        folder = _products(unit.share_fallback.root)
        unit.report_failure(FilingArea.SHARE, "x", _net_error())

        assert unit.drain_fallbacks() == (0, 0)
        assert os.path.isfile(os.path.join(folder, "a.fits"))

    def test_a_promotion_drains_it_at_once(self, unit):
        unit.report_failure(FilingArea.SHARE, "x", _net_error())
        _products(unit.share_fallback.root)

        assert unit.check_now(FilingArea.SHARE)
        _join_drain()

        assert os.path.isfile(unit.shared.root + REL + "/a.fits")

    def test_a_differing_copy_on_the_share_stays_in_the_fallback(self, unit):
        _products(unit.share_fallback.root, names=("a.fits",))
        os.makedirs(unit.shared.root + REL)
        with open(unit.shared.root + REL + "/a.fits", "wb") as f:
            f.write(b"something else")

        unit.drain_fallbacks()

        assert os.path.isfile(unit.share_fallback.root + REL + "/a.fits"), "never overwritten"


class TestTheRamFallback:
    def test_its_products_go_to_the_share(self, unit):
        _products(unit.ram_fallback.root)

        assert unit.drain_fallbacks() == (1, 0)
        assert unit.flush(timeout=10)

        assert sorted(os.listdir(unit.shared.root + REL)) == ["a.fits", "b.fits"]

    def test_a_folder_a_producer_has_claimed_is_left(self, unit):
        folder = _products(unit.ram_fallback.root)
        assert MoveGuardian().claim_folder(folder)

        assert unit.drain_fallbacks() == (0, 0)
        assert os.path.isfile(os.path.join(folder, "a.fits"))

    def test_with_the_share_down_they_spill_to_the_share_fallback(self, unit):
        unit.report_failure(FilingArea.SHARE, "x", _net_error())
        _products(unit.ram_fallback.root)

        unit.drain_fallbacks()
        assert unit.flush(timeout=10)

        assert os.path.isfile(unit.share_fallback.root + REL + "/a.fits")


class TestWhenItRuns:
    def test_one_at_a_time(self, unit):
        _products(unit.share_fallback.root)
        assert Filer._drain_lock.acquire(blocking=False)
        try:
            assert unit.drain_fallbacks() == (0, 0)
        finally:
            Filer._drain_lock.release()
        assert unit.drain_fallbacks() == (0, 1)

    def test_at_start_up_with_the_relocation_sweep(self, unit):
        _products(unit.share_fallback.root)

        unit._relocate_products()

        assert os.path.isfile(unit.shared.root + REL + "/a.fits")

    def test_without_fallbacks_nothing_happens(self, unit):
        unit.ram_fallback = None
        unit.share_fallback = None
        assert unit.drain_fallbacks() == (0, 0)


def _join_drain(timeout: float = 5.0) -> None:
    for thread in threading.enumerate():
        if thread.name == "fallback-drain":
            thread.join(timeout)
