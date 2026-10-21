"""Every path PathMaker hands out is forward-slashed, on every platform.

The roots are stored forward-slashed ("D:/MAST/"); PathMaker used to return ``str(Path(...))``,
backslashed on Windows. A prefix test or ``.replace`` between the two then silently did nothing
-- how acquisition plots were pointed at the RAM disk (plans/filing-areas-design.md 1.4).

The suite's roots are temporary folders in the native spelling (common.testing.filer), so on
the Windows runner they are backslashed: these tests check that PathMaker converts, not that
it was handed forward slashes.
"""

from __future__ import annotations

import platform

import pytest

from common.paths import PathMaker, as_posix


def _every_path() -> dict[str, str]:
    pm = PathMaker()
    return {
        "make_observing_night_folder": pm.make_observing_night_folder(),
        "make_exposures_folder": pm.make_exposures_folder(),
        "make_autofocus_folder": pm.make_autofocus_folder(),
        "make_autofocus_folder(subfolder)": pm.make_autofocus_folder(subfolder="highspec"),
        "make_calibration_folder": pm.make_calibration_folder("focuser"),
        "make_acquisition_folder": pm.make_acquisition_folder(),
        "make_acquisition_folder(phase, tags)": pm.make_acquisition_folder(phase="sky", tags={"target": "12.5,20.2"}),
        "make_guidings_folder": pm.make_guidings_folder(),
        "make_spirals_folder": pm.make_spirals_folder(),
        "make_flux_metering_folder": pm.make_flux_metering_folder(),
        "make_plans_folder": PathMaker.make_plans_folder(),
        "make_plans_files_folder": PathMaker.make_plans_files_folder(),
        "make_run_folder": PathMaker.make_run_folder(),
        "make_spec_acquisitions_folder": PathMaker.make_spec_acquisitions_folder("highspec"),
        "make_spec_exposures_folder(band)": PathMaker.make_spec_exposures_folder("deepspec", band="U"),
    }


def test_no_pathmaker_path_has_a_backslash():
    offenders = {name: path for name, path in _every_path().items() if "\\" in path}
    assert not offenders, offenders


def test_an_empty_tag_is_named_not_left_as_a_template():
    folder = PathMaker().make_acquisition_folder(tags={"dry": None})
    assert folder.endswith(",dry")


@pytest.mark.skipif(platform.system() != "Windows", reason="backslash is a separator only on Windows")
class TestOnWindows:
    def test_a_drive_path_is_forward_slashed(self):
        assert as_posix("D:\\MAST\\2026-10-21\\Exposures") == "D:/MAST/2026-10-21/Exposures"

    def test_a_unc_path_keeps_its_leading_double_slash(self):
        assert as_posix("\\\\mast-ns-control\\mast-share\\MAST\\mast00") == "//mast-ns-control/mast-share/MAST/mast00"


def test_a_forward_slashed_path_is_unchanged():
    assert as_posix("D:/MAST/2026-10-21/Exposures") == "D:/MAST/2026-10-21/Exposures"
