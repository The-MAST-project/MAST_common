"""The ``phd2.handover`` section: which handover an FCU v2 unit runs (MAST_unit#30).

The legacy handover -- fold mirror in before guiding -- is the default, so a unit
with no section, or with a section that predates ``style``, gets it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("common.config.phd2", reason="config package import chain unavailable")
from pydantic import ValidationError  # noqa: E402

from common.config.phd2 import HandoverConfig, HandoverStyle, PHD2Config  # noqa: E402

PHD2_DOC = {
    "profile": "PWI4+ASI-native,binning=1,bpp=16",
    "settle": {"pixels": 1, "time": 0, "timeout": 0},
    "validation_interval": 0.0,
}


def test_the_default_style_is_legacy():
    assert HandoverConfig().style is HandoverStyle.LEGACY


def test_a_unit_doc_without_the_section_gets_legacy():
    assert PHD2Config.model_validate(PHD2_DOC).handover.style is HandoverStyle.LEGACY


def test_a_section_from_before_style_still_parses():
    conf = PHD2Config.model_validate({**PHD2_DOC, "handover": {"auto_insert": False}})
    assert (conf.handover.style, conf.handover.auto_insert) == (HandoverStyle.LEGACY, False)


def test_bracketed_is_selectable():
    assert HandoverConfig.model_validate({"style": "bracketed"}).style is HandoverStyle.BRACKETED


def test_an_unknown_style_is_refused():
    with pytest.raises(ValidationError):
        HandoverConfig.model_validate({"style": "sideways"})
