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
