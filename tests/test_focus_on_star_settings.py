"""focus_on_star's shared pieces: its settings block, its activity flag, its folder."""

from common.activities import UnitActivities
from common.config.calibration import CalibrationConfig, CalibrationSettings, FocusOnStarSettings
from common.paths import CALIBRATION_PHASE_FOLDERS


def test_defaults_are_the_agreed_ones():
    s = FocusOnStarSettings()
    assert s.catalog_root == r"\\last0\catalogs\GAIA\DR3"
    assert s.mag_column == "phot_g_mean_mag"
    assert s.fov_percent == 10.0
    assert s.steps == 5
    assert s.steps % 2 == 1  # a V-curve point must sit on the seed
    assert s.ticks_per_step == 50
    assert s.acquisition_tries == 5  # its own limit, not the unit's acquisition.tries


def test_a_stored_focus_on_star_block_without_acquisition_tries_still_loads():
    # Blocks written before the field existed default it rather than fail.
    settings = CalibrationSettings.model_validate({"focus_on_star": {"steps": 7}})
    assert settings.focus_on_star.steps == 7
    assert settings.focus_on_star.acquisition_tries == 5


def test_a_stored_block_without_focus_on_star_still_loads():
    # Every unit entry in the DB predates the block; it must default, not fail.
    stored = {"settings": {"focuser": {"images": 9}}, "products": {}}
    conf = CalibrationConfig.model_validate(stored)
    assert conf.settings.focuser.images == 9
    assert conf.settings.focus_on_star == FocusOnStarSettings()


def test_per_unit_override_is_honoured():
    settings = CalibrationSettings.model_validate({"focus_on_star": {"star_magnitude": 11.5}})
    assert settings.focus_on_star.star_magnitude == 11.5
    assert settings.focus_on_star.steps == 5


def test_flag_is_appended_after_aborting():
    # Appending keeps every existing member's number: MAST_control and MAST_gui compare them.
    members = list(UnitActivities)
    assert members[-1] is UnitActivities.FocusingOnStar
    assert UnitActivities.FocusingOnStar == UnitActivities.Aborting << 1


def test_folder_name():
    assert CALIBRATION_PHASE_FOLDERS["focus_on_star"] == "FocusOnStar"
