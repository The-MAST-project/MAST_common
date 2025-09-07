from pydantic import BaseModel, model_validator

import common.ASI as ASI

from .rois import RoiConfig


class ImagerBinningConfig(BaseModel):
    """Configuration for the imager binning."""

    x: int
    y: int

    @model_validator(mode="after")
    def validate_binning(self):
        if self.x <= 0 or self.y <= 0:
            raise ValueError("Binning values must be positive integers.")
        return self


class OffsetConfig(BaseModel):
    x: int
    y: int


class ImagerConfig(BaseModel):
    """Configuration for the imager."""

    imager_type: str
    valid_imager_types: list[str]
    # power: PowerSwitchConfig | None = None
    offset: OffsetConfig | None = None
    roi: RoiConfig | None = None
    temp_check_interval: int = 60
    pixel_scale_at_bin1: float
    format: ASI.ValidOutputFormats
    gain: int
