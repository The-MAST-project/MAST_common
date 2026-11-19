"""`SolvingConfig.source_selection`: optional, off by default, strict about its keys."""

import pytest
from pydantic import ValidationError

from common.config.unit import SolvingConfig, SourceSelectionConfig


def test_absent_by_default():
    assert SolvingConfig(method="m", valid_methods=["m"]).source_selection is None


def test_mode_defaults_to_original_and_parameters_to_none():
    cfg = SourceSelectionConfig()
    assert cfg.mode == "original"
    assert all(v is None for k, v in cfg.model_dump().items() if k != "mode")


def test_accepts_the_documented_fragment():
    cfg = SourceSelectionConfig(mode="selective", min_snr=12.0, grid=(4, 3), exclusion_half_width=None)
    assert cfg.mode == "selective"
    assert cfg.grid == (4, 3)


@pytest.mark.parametrize("bad", [{"mode": "on"}, {"min_sn": 3.0}])
def test_rejects_unknown_mode_or_key(bad):
    with pytest.raises(ValidationError):
        SourceSelectionConfig(**bad)
