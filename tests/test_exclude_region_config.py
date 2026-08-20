"""Regression tests for the persisted PHD2 exclusion-region configuration.

Guards the ``phd2.exclude_region`` contract (MAST_common#13 / MAST_unit#30,
issue MAST_unit#19): the ``mode`` discriminator (off | fixed), the
rectangle-matches-mode rule and its one deliberate asymmetry with
``phd2.limit_frame``, the depth/pad derivation guard, and that existing DB
``units`` documents without the section parse unchanged.

Needs the config package's import chain installed (matplotlib, pymongo, ...)
but no Mongo server and no hardware.
"""

from __future__ import annotations

import pytest

phd2_config = pytest.importorskip("common.config.phd2", reason="config package import chain unavailable")
from pydantic import ValidationError  # noqa: E402

from common.config.identification import UserCapabilities  # noqa: E402
from common.config.phd2 import ExcludeRegionConfig, ExcludeRegionMode, PHD2Config  # noqa: E402

# The ``phd2`` section of the real 'common' units doc, as in
# test_limit_frame_config.py: predates both PHD2 sections, so it is the
# backward-compatibility target.
LEGACY_COMMON_PHD2_DOC = {
    "profile": "PWI4+ASI-native,binning=1,bpp=16",
    "settle": {"pixels": 1, "time": 0, "timeout": 0},
    "validation_interval": 0.0,
}

# The 2026-07-05 measured fold-mirror band at an illustrative depth/pad.
BAND = {"x": 2219, "y": 0, "width": 3850, "height": 5644}
DERIVATION = {"depth": 0.5, "pad_px": 100, "derived_from_depth": 0.5, "derived_from_pad_px": 100}


class TestExcludeRegionConfigModel:
    def test_default_mode_is_off(self):
        """Absent config == no exclusion region, the only safe default.

        Unlike the limit frame there is no sensible derived fallback rectangle:
        the mirror shadow must be measured per unit before the feature can do
        anything but suppress guide stars for no reason.
        """
        er = ExcludeRegionConfig()
        assert er.mode is ExcludeRegionMode.OFF
        assert (er.x, er.y, er.width, er.height) == (0, 0, 0, 0)
        assert er.has_roi is False

    def test_fixed_with_complete_rectangle_parses_verbatim(self):
        er = ExcludeRegionConfig(mode="fixed", **BAND)
        assert er.mode is ExcludeRegionMode.FIXED
        assert (er.x, er.y, er.width, er.height) == (2219, 0, 3850, 5644)
        assert er.has_roi is True

    @pytest.mark.parametrize(
        "rect",
        [
            {},
            {"width": 3850},
            {"height": 5644},
            {"x": 2219, "y": 0},
        ],
    )
    def test_fixed_without_complete_rectangle_rejected(self, rect):
        with pytest.raises(ValidationError, match="requires a complete rectangle"):
            ExcludeRegionConfig(mode="fixed", **rect)

    def test_measured_rectangle_survives_mode_off(self):
        """The deliberate asymmetry with phd2.limit_frame.

        A limit-frame rectangle under a non-fixed mode is a contradiction. Here it
        is the expected workflow: the shadow-measurement tool writes each unit's
        band (and its derivation record) as soon as it is measured, and the region
        is switched on later, per unit. Rejecting the pair would force an operator
        to delete a measurement in order to disable the feature -- and would make
        'measured but not yet enabled' inexpressible.
        """
        er = ExcludeRegionConfig(mode="off", **BAND, **DERIVATION)
        assert er.mode is ExcludeRegionMode.OFF
        assert er.has_roi is True
        assert er.stale_derivation() is None

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValidationError):
            ExcludeRegionConfig(mode="enabled")

    @pytest.mark.parametrize("field", ["x", "y", "width", "height"])
    def test_negative_pixel_values_rejected(self, field):
        rect = dict(BAND, **{field: -1})
        with pytest.raises(ValidationError):
            ExcludeRegionConfig(mode="fixed", **rect)

    @pytest.mark.parametrize("depth", [0.0, -0.1, 1.5])
    def test_depth_outside_the_unit_interval_rejected(self, depth):
        with pytest.raises(ValidationError):
            ExcludeRegionConfig(mode="fixed", **BAND, depth=depth)

    def test_negative_pad_rejected(self):
        with pytest.raises(ValidationError):
            ExcludeRegionConfig(mode="fixed", **BAND, pad_px=-1)

    def test_editable_fields_carry_gui_capability_metadata(self):
        """The GUI contract: everything an operator may edit is capability-gated.

        The ``derived_from_*`` record is deliberately NOT editable -- the
        measurement tool is its sole writer -- so it is exempt from the
        capability requirement and asserted read-only instead.
        """
        for name, field in ExcludeRegionConfig.model_fields.items():
            extra = field.json_schema_extra
            assert isinstance(extra, dict), f"{name}: missing json_schema_extra"
            if name.startswith("derived_from_"):
                assert extra["ui"]["editable"] is False, f"{name}: must stay read-only"
                continue
            assert extra["ui"]["editable"] is True, f"{name}: not GUI-editable"
            assert UserCapabilities.CAN_CHANGE_CONFIGURATION.value in extra["required_capabilities"], (
                f"{name}: missing CAN_CHANGE_CONFIGURATION"
            )

    def test_mode_select_offers_every_mode(self):
        options = ExcludeRegionConfig.model_fields["mode"].json_schema_extra["ui"]["options"]
        assert options == [m.value for m in ExcludeRegionMode]


class TestStaleDerivation:
    def test_no_knobs_is_never_stale(self):
        """A legacy rectangle with no depth/pad knobs predates the derivation record."""
        er = ExcludeRegionConfig(mode="fixed", **BAND)
        assert er.stale_derivation() is None

    def test_matching_record_is_not_stale(self):
        er = ExcludeRegionConfig(mode="fixed", **BAND, **DERIVATION)
        assert er.stale_derivation() is None

    def test_knobs_without_a_derivation_record_are_stale(self):
        er = ExcludeRegionConfig(mode="fixed", **BAND, depth=0.5, pad_px=100)
        assert "no derivation record" in (er.stale_derivation() or "")

    def test_depth_mismatch_is_stale(self):
        er = ExcludeRegionConfig(mode="fixed", **BAND, **dict(DERIVATION, depth=0.1))
        assert "derived at depth" in (er.stale_derivation() or "")

    def test_pad_mismatch_is_stale(self):
        er = ExcludeRegionConfig(mode="fixed", **BAND, **dict(DERIVATION, pad_px=250))
        assert "derived with pad_px" in (er.stale_derivation() or "")

    def test_float_epsilon_on_depth_is_not_stale(self):
        er = ExcludeRegionConfig(
            mode="fixed", **BAND, **dict(DERIVATION, depth=0.5 + 1e-12)
        )
        assert er.stale_derivation() is None

    def test_knobs_before_a_rectangle_are_not_stale(self):
        """Setting depth/pad ahead of the measurement is a legal intermediate state."""
        er = ExcludeRegionConfig(mode="off", depth=0.5, pad_px=100)
        assert er.has_roi is False
        assert er.stale_derivation() is None


class TestPHD2ConfigCompatibility:
    def test_legacy_doc_without_section_parses_unchanged(self):
        conf = PHD2Config(**LEGACY_COMMON_PHD2_DOC)
        assert conf.exclude_region.mode is ExcludeRegionMode.OFF
        assert conf.profile == LEGACY_COMMON_PHD2_DOC["profile"]

    def test_doc_with_fixed_section_parses(self):
        doc = dict(LEGACY_COMMON_PHD2_DOC, exclude_region=dict(BAND, mode="fixed", **DERIVATION))
        conf = PHD2Config(**doc)
        assert conf.exclude_region.mode is ExcludeRegionMode.FIXED
        assert conf.exclude_region.width == 3850
        assert conf.exclude_region.stale_derivation() is None

    def test_doc_with_off_section_and_measured_band_parses(self):
        doc = dict(LEGACY_COMMON_PHD2_DOC, exclude_region=dict(BAND, mode="off", **DERIVATION))
        conf = PHD2Config(**doc)
        assert conf.exclude_region.mode is ExcludeRegionMode.OFF
        assert conf.exclude_region.has_roi is True
