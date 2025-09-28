from pydantic import BaseModel

import common.ASI as ASI

from .rois import RoiConfig

# class ImagerBinningConfig(BaseModel):
#     """Configuration for the imager binning."""

#     x: int
#     y: int

#     @model_validator(mode="after")
#     def validate_binning(self):
#         from common.ASI import ASI_294MM_SUPPORTED_BINNINGS

#         if any([v not in ASI_294MM_SUPPORTED_BINNINGS for v in [self.x, self.y]]):
#             raise ValueError(f"Binning values ({self.x=}, {self.y=}) must be one of {ASI_294MM_SUPPORTED_BINNINGS=}")

#         if self.x != self.y:
#             raise ValueError(f"Unequal horizontal/vertical binning values ({self.x=} != {self.y=})")

#         return self


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
