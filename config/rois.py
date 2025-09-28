from enum import Enum

from pydantic import BaseModel


class SkyRoiConfig(BaseModel):
    """Configuration for the region of interest (ROI) in the sky image."""

    sky_x: int
    sky_y: int
    width: int
    height: int


class SpecRoiConfig(BaseModel):
    """Configuration for the region of interest (ROI) in the spectrograph."""

    margin_horizontal: int
    margin_vertical: int
    fiber_x: int
    fiber_y: int


class RoiConfig(BaseModel):
    """Configuration for the region of interest (ROI) in the camera."""

    x: int
    y: int
    width: int
    height: int

class FcuVersion(str, Enum):
    v1 = "fcu_v1"
    v2 = "fcu_v2"

RoisConfig = dict[FcuVersion, RoiConfig | SkyRoiConfig | SpecRoiConfig]
