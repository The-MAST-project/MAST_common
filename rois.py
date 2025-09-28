from pydantic import BaseModel


class UnitRoi:
    """
    In unit terms a region-of-interest is centered on a pixel and has width and height
    """

    center_x: int
    center_y: int
    width: int
    height: int

    def __init__(self, _x: int, _y: int, width: int, height: int):
        self.center_x = _x
        self.center_y = _y
        self.width = width
        self.height = height

    def __repr__(self) -> str:
        return f"UnitRoi(center_x={self.center_x},center_y={self.center_y},width={self.width},height={self.height})"

class SkyRoi(BaseModel):
    """Configuration for the region of interest (ROI) in the sky image."""
    sky_x: int
    sky_y: int
    width: int
    height: int

    def __repr__(self) -> str:
        return f"SkyRoi(center_x={self.sky_x},center_y={self.sky_y},width={self.width},height={self.height})"

class SpecRoi(BaseModel):
    """Configuration for the region of interest (ROI) in the spectrograph."""
    width: int
    height: int
    fiber_x: int
    fiber_y: int

    def __repr__(self) -> str:
        return f"SpecRoi(center_x={self.fiber_x},center_y={self.fiber_y},width={self.width},height={self.height})"
