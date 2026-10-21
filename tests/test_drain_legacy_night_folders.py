"""The legacy ``C:/MAST/<night>/`` folders are drained to the share (filing-areas plan 6.3).

The old construction-time fallback put a process's logs (and on mast00 one night's
products) under ``C:/MAST/<night>/`` whenever it started with the share unreachable. They
are swept like a fallback -- only folders named for a night, never anything else under
``C:/MAST/``, never tonight's -- and their logs renamed ``*-log.fallback.txt`` so they do not
collide with the share's own log of the same night.
"""

from __future__ import annotations

import datetime
import logging
import os

import pytest

import common.filer as filer_module
from common.filer import Filer, FilingArea, Location
from common.mast_logging import observing_night
from common.testing.filer import reset_filing_areas

NIGHT = "2026-04-19"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(filer_module, "_probe_cache", {})
    monkeypatch.setattr(filer_module, "_probe_threads", {})
    monkeypatch.setattr(Filer, "MONITOR_INTERVAL_SECONDS", 3600.0)
    monkeypatch.setattr(Filer, "_mover_pool", None)
    monkeypatch.setattr(Filer, "_ensure_sweeper", lambda self: None)
    reset_filing_areas()
    yield
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
        self.logger = logging.getLogger("test.legacy-nights")

    monkeypatch.setattr(Filer, "__init__", fake_init)
    return Filer()


def _write(path: str, data: bytes = b"x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def test_a_night_folder_moves_with_its_logs_renamed(unit):
    local = unit.local.root
    _write(f"{local}{NIGHT}/mast-unit-log.txt", b"the fallback's log")
    _write(f"{local}{NIGHT}/mast-STARTUP-log.txt")
    _write(f"{local}{NIGHT}/mast-unit-supervisor-log.txt")
    _write(f"{local}{NIGHT}/Acquisitions/seq=0001/frame.fits")

    unit.drain_fallbacks()

    night = unit.shared.root + NIGHT
    assert sorted(os.listdir(night)) == [
        "Acquisitions",
        "mast-STARTUP-log.fallback.txt",
        "mast-unit-log.fallback.txt",
        "mast-unit-supervisor-log.fallback.txt",
    ]
    assert os.path.isfile(f"{night}/Acquisitions/seq=0001/frame.fits")
    assert not os.path.exists(f"{local}{NIGHT}")


def test_the_shares_own_log_of_the_night_is_kept_beside_it(unit):
    _write(f"{unit.local.root}{NIGHT}/mast-unit-log.txt", b"from C:")
    _write(f"{unit.shared.root}{NIGHT}/mast-unit-log.txt", b"from the share")

    unit.drain_fallbacks()

    night = unit.shared.root + NIGHT
    with open(f"{night}/mast-unit-log.txt", "rb") as f:
        assert f.read() == b"from the share"
    with open(f"{night}/mast-unit-log.fallback.txt", "rb") as f:
        assert f.read() == b"from C:"


@pytest.mark.parametrize(
    "name",
    ["Shared", "iers-cache", "config-db-cache", "Kepler", "Campaigns", "2026-13-45", "2026-4-19", "20261019"],
)
def test_nothing_else_under_the_local_root_is_touched(unit, name):
    path = _write(f"{unit.local.root}{name}/keep.bin")

    unit.drain_fallbacks()

    assert os.path.isfile(path)


def test_a_file_at_the_top_of_the_local_root_is_touched_neither(unit):
    path = _write(f"{unit.local.root}calibrate.ps1")
    unit.drain_fallbacks()
    assert os.path.isfile(path)


def test_tonights_folder_is_left_its_log_may_be_open(unit):
    tonight = observing_night(datetime.datetime.now(datetime.UTC))
    path = _write(f"{unit.local.root}{tonight}/mast-unit-log.txt")

    unit.drain_fallbacks()

    assert os.path.isfile(path), "neither renamed nor moved"


def test_not_while_the_share_is_down(unit):
    path = _write(f"{unit.local.root}{NIGHT}/mast-unit-log.txt")
    exc = OSError(None, "net")
    exc.winerror = 53  # type: ignore[attr-defined]
    unit.report_failure(FilingArea.SHARE, "x", exc)

    unit.drain_fallbacks()

    assert os.path.isfile(path)


def test_never_where_the_local_disk_is_the_share(unit):
    # Control: /Storage/mast-share/MAST is both. Its top-level night folders are the share's.
    unit.local = unit.share_root
    path = _write(f"{unit.share_root.root}{NIGHT}/mast-control-log.txt")

    assert unit._drain_legacy_night_folders() == 0
    assert os.path.isfile(path)


def test_a_folders_bookkeeping_stays(unit):
    _write(f"{unit.local.root}{NIGHT}/Acquisitions/frame.fits")
    _write(f"{unit.local.root}{NIGHT}/Acquisitions/seq.txt", b"3")

    unit.drain_fallbacks()

    assert os.path.isfile(f"{unit.shared.root}{NIGHT}/Acquisitions/frame.fits")
    assert os.path.isfile(f"{unit.local.root}{NIGHT}/Acquisitions/seq.txt")
