"""PathMaker's RAM folders follow the RAM area: the disk, or its fallback while it is gone.

plans/filing-areas-design.md 7.1. Folders used to be made under `Filer().ram.root`, the root
chosen when the Filer was built, so after the RAM disk died every new acquisition failed
against it. Now each folder asks for the current root.
"""

from __future__ import annotations

import errno

import pytest

import common.filer as filer_module
from common.filer import Filer, FilingArea
from common.paths import PathMaker
from common.testing.filer import DiskUsage, reset_filing_areas


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(filer_module, "_probe_cache", {})
    monkeypatch.setattr(filer_module, "_probe_threads", {})
    monkeypatch.setattr(Filer, "MONITOR_INTERVAL_SECONDS", 3600.0)
    reset_filing_areas()
    yield
    reset_filing_areas()


def _folders() -> list[str]:
    pm = PathMaker()
    return [
        pm.make_observing_night_folder(),
        pm.make_exposures_folder(),
        pm.make_autofocus_folder(),
        pm.make_calibration_folder("focuser"),
        pm.make_acquisition_folder(),
        pm.make_guidings_folder(),
        pm.make_spirals_folder(),
        pm.make_flux_metering_folder(),
        PathMaker.make_spec_acquisitions_folder("highspec"),
        PathMaker.make_spec_exposures_folder("deepspec", band="U"),
    ]


def _demote_ram():
    Filer().report_failure(FilingArea.RAM, "x", OSError(errno.ENOSPC, "No space left on device"))


def test_on_the_ram_disk_normally():
    ram = Filer().ram.root
    assert all(folder.startswith(ram) for folder in _folders())


def test_on_the_ram_fallback_while_the_disk_is_demoted():
    _demote_ram()
    fallback = Filer().ram_fallback.root

    folders = _folders()

    assert all(folder.startswith(fallback) for folder in folders), folders


def test_back_on_the_disk_once_it_is_promoted():
    _demote_ram()
    assert Filer().check_now(FilingArea.RAM)

    ram = Filer().ram.root
    assert all(folder.startswith(ram) for folder in _folders())


def test_nowhere_to_write_raises_enospc(monkeypatch):
    monkeypatch.setattr(filer_module, "_disk_usage", lambda path: DiskUsage(total=100, used=95, free=5))
    _demote_ram()

    with pytest.raises(OSError) as raised:
        PathMaker().make_exposures_folder()
    assert raised.value.errno == errno.ENOSPC


def test_an_explicit_root_is_still_used(tmp_path):
    _demote_ram()
    folder = PathMaker().make_exposures_folder(root=str(tmp_path))
    assert folder.startswith(tmp_path.as_posix())
