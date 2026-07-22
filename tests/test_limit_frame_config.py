"""Regression tests for the persisted PHD2 limit-frame configuration.

Guards the ``phd2.limit_frame`` contract (MAST_common#12 / MAST_unit#29,
issue MAST_unit#51): the model defaults, the ``has_roi`` "not configured"
convention (width/height 0), and — critically — that existing DB ``units``
documents without the section parse unchanged, so landing the feature cannot
alter deployed behavior.

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
from common.config.phd2 import LimitFrameConfig, PHD2Config  # noqa: E402

# The ``phd2`` section of the real 'common' units doc (backup
# units-2026-04-29T18:26:42Z.ndjson), with Mongo extended-JSON numbers folded
# to the native types pymongo delivers. Predates limit_frame: the
# backward-compatibility target.
LEGACY_COMMON_PHD2_DOC = {
    "profile": "PWI4+ASI-native,binning=1,bpp=16",
    "settle": {"pixels": 1, "time": 0, "timeout": 0},
    "validation_interval": 0.0,
}


class TestLimitFrameConfigModel:
    def test_defaults_mean_derived_roi(self):
        """Absent config == enabled with no rectangle: derive from guiding.rois."""
        lf = LimitFrameConfig()
        assert lf.enabled is True
        assert (lf.x, lf.y, lf.width, lf.height) == (0, 0, 0, 0)
        assert lf.has_roi is False

    @pytest.mark.parametrize(
        ("width", "height", "expected"),
        [
            (0, 0, False),
            (2000, 0, False),
            (0, 400, False),
            (2000, 400, True),
            (1, 1, True),
        ],
    )
    def test_has_roi_requires_both_dimensions(self, width, height, expected):
        lf = LimitFrameConfig(x=3031, y=2692, width=width, height=height)
        assert lf.has_roi is expected

    @pytest.mark.parametrize("field", ["x", "y", "width", "height"])
    def test_negative_pixel_values_rejected(self, field):
        with pytest.raises(ValidationError):
            LimitFrameConfig(**{field: -1})

    def test_explicit_rectangle_parses_verbatim(self):
        lf = LimitFrameConfig(enabled=True, x=3031, y=2692, width=2000, height=400)
        assert (lf.x, lf.y, lf.width, lf.height) == (3031, 2692, 2000, 400)
        assert lf.has_roi is True

    def test_all_fields_carry_gui_capability_metadata(self):
        """The GUI contract: every field is editable-with-capability."""
        for name, field in LimitFrameConfig.model_fields.items():
            extra = field.json_schema_extra
            assert isinstance(extra, dict), f"{name}: missing json_schema_extra"
            assert extra["ui"]["editable"] is True, f"{name}: not GUI-editable"
            assert UserCapabilities.CAN_CHANGE_CONFIGURATION.value in extra["required_capabilities"], (
                f"{name}: missing CAN_CHANGE_CONFIGURATION"
            )


class TestPHD2ConfigCompatibility:
    def test_legacy_doc_without_section_parses_unchanged(self):
        """Existing DB docs predate limit_frame: they must parse and behave as today."""
        conf = PHD2Config(**LEGACY_COMMON_PHD2_DOC)
        assert conf.limit_frame.enabled is True
        assert conf.limit_frame.has_roi is False
        assert conf.profile == LEGACY_COMMON_PHD2_DOC["profile"]

    def test_doc_with_section_parses(self):
        doc = dict(
            LEGACY_COMMON_PHD2_DOC,
            limit_frame={"enabled": True, "x": 3031, "y": 2692, "width": 2000, "height": 400},
        )
        conf = PHD2Config(**doc)
        assert conf.limit_frame.has_roi is True
        assert (conf.limit_frame.x, conf.limit_frame.y) == (3031, 2692)

    def test_doc_with_disabled_section_parses(self):
        doc = dict(LEGACY_COMMON_PHD2_DOC, limit_frame={"enabled": False})
        conf = PHD2Config(**doc)
        assert conf.limit_frame.enabled is False
        assert conf.limit_frame.has_roi is False
