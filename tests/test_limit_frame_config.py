"""Regression tests for the persisted PHD2 limit-frame configuration.

Guards the ``phd2.limit_frame`` contract (MAST_common#12 / MAST_unit#29,
issue MAST_unit#51): the ``mode`` discriminator (derived | full_frame |
fixed), the rectangle-matches-mode validation, and — critically — that
existing DB ``units`` documents without the section parse unchanged, so
landing the feature cannot alter deployed behavior.

Needs the config package's import chain installed (matplotlib, pymongo, ...)
but no Mongo server and no hardware.
"""

from __future__ import annotations

import pytest

phd2_config = pytest.importorskip(
    "common.config.phd2", reason="config package import chain unavailable"
)
from pydantic import ValidationError  # noqa: E402

from common.config.identification import UserCapabilities  # noqa: E402
from common.config.phd2 import LimitFrameConfig, LimitFrameMode, PHD2Config  # noqa: E402

# The ``phd2`` section of the real 'common' units doc (backup
# units-2026-04-29T18:26:42Z.ndjson), with Mongo extended-JSON numbers folded
# to the native types pymongo delivers. Predates limit_frame: the
# backward-compatibility target.
LEGACY_COMMON_PHD2_DOC = {
    "profile": "PWI4+ASI-native,binning=1,bpp=16",
    "settle": {"pixels": 1, "time": 0, "timeout": 0},
    "validation_interval": 0.0,
}

RECT = {"x": 3031, "y": 2692, "width": 2000, "height": 400}


class TestLimitFrameConfigModel:
    def test_default_mode_is_derived(self):
        """Absent config == derived guiding ROI: today's deployed behavior."""
        lf = LimitFrameConfig()
        assert lf.mode is LimitFrameMode.DERIVED
        assert (lf.x, lf.y, lf.width, lf.height) == (0, 0, 0, 0)

    def test_fixed_with_complete_rectangle_parses_verbatim(self):
        lf = LimitFrameConfig(mode="fixed", **RECT)
        assert lf.mode is LimitFrameMode.FIXED
        assert (lf.x, lf.y, lf.width, lf.height) == (3031, 2692, 2000, 400)

    def test_fixed_at_sensor_origin_is_legal(self):
        lf = LimitFrameConfig(mode="fixed", width=2000, height=400)
        assert (lf.x, lf.y) == (0, 0)

    @pytest.mark.parametrize(
        "rect",
        [
            {},
            {"width": 2000},
            {"height": 400},
            {"x": 3031, "y": 2692},
        ],
    )
    def test_fixed_without_complete_rectangle_rejected(self, rect):
        with pytest.raises(ValidationError, match="requires a complete rectangle"):
            LimitFrameConfig(mode="fixed", **rect)

    @pytest.mark.parametrize("mode", ["derived", "full_frame"])
    def test_rectangle_under_other_modes_is_a_contradiction(self, mode):
        """A configured rect must never be silently ignored."""
        with pytest.raises(ValidationError, match="applies only to mode 'fixed'"):
            LimitFrameConfig(mode=mode, **RECT)

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValidationError):
            LimitFrameConfig(mode="enabled")

    @pytest.mark.parametrize("field", ["x", "y", "width", "height"])
    def test_negative_pixel_values_rejected(self, field):
        rect = dict(RECT, **{field: -1})
        with pytest.raises(ValidationError):
            LimitFrameConfig(mode="fixed", **rect)

    def test_all_fields_carry_gui_capability_metadata(self):
        """The GUI contract: every field is editable-with-capability."""
        for name, field in LimitFrameConfig.model_fields.items():
            extra = field.json_schema_extra
            assert isinstance(extra, dict), f"{name}: missing json_schema_extra"
            assert extra["ui"]["editable"] is True, f"{name}: not GUI-editable"
            assert UserCapabilities.CAN_CHANGE_CONFIGURATION.value in extra["required_capabilities"], (
                f"{name}: missing CAN_CHANGE_CONFIGURATION"
            )

    def test_mode_select_offers_every_mode(self):
        options = LimitFrameConfig.model_fields["mode"].json_schema_extra["ui"]["options"]
        assert options == [m.value for m in LimitFrameMode]


class TestPHD2ConfigCompatibility:
    def test_legacy_doc_without_section_parses_unchanged(self):
        """Existing DB docs predate limit_frame: they must parse and behave as today."""
        conf = PHD2Config(**LEGACY_COMMON_PHD2_DOC)
        assert conf.limit_frame.mode is LimitFrameMode.DERIVED
        assert conf.profile == LEGACY_COMMON_PHD2_DOC["profile"]

    def test_doc_with_fixed_section_parses(self):
        doc = dict(LEGACY_COMMON_PHD2_DOC, limit_frame=dict(RECT, mode="fixed"))
        conf = PHD2Config(**doc)
        assert conf.limit_frame.mode is LimitFrameMode.FIXED
        assert (conf.limit_frame.x, conf.limit_frame.y) == (3031, 2692)

    def test_doc_with_full_frame_section_parses(self):
        doc = dict(LEGACY_COMMON_PHD2_DOC, limit_frame={"mode": "full_frame"})
        conf = PHD2Config(**doc)
        assert conf.limit_frame.mode is LimitFrameMode.FULL_FRAME
