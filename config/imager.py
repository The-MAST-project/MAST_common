from pydantic import BaseModel, Field

from common import asi

from .rois import RoiConfig

# class ImagerBinningConfig(BaseModel):
#     """Configuration for the imager binning."""

#     x: int
#     y: int

#     @model_validator(mode="after")
#     def validate_binning(self):
#         from common.asi import ASI_294MM_SUPPORTED_BINNINGS

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
    # Applied at startup by backends that can set a target (ZWO; ASCOM when the driver allows).
    # PHD2 cannot -- its set point is the PHD2 profile's -- so there it is only compared, and a
    # mismatch warned about. Conservative by default: a modest cool, kept as the fleet value.
    cooler_set_point: float = Field(
        default=5.0,
        ge=-40.0,
        le=30.0,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "number",
                "unit": "C",
                "label": "Cooler Set Point",
                "tooltip": "Camera sensor target temperature; applied by ZWO/ASCOM, compared with the PHD2 profile's",
            },
        },
    )
    pixel_scale_at_bin1: float = Field(gt=0.0, lt=10.0)
    format: asi.ValidOutputFormats
    gain: int
