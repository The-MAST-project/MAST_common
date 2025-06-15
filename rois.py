from pydantic import BaseModel

from imagers import ImagerBinning, ImagerRoi


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

    def to_imager_roi(self, binning: ImagerBinning | None = None) -> ImagerRoi:
        """
        An imager (e.g. ASCOM camera) ROI has a starting pixel (x, y) at lower left corner, width and height
        """
        if not binning:
            binning = ImagerBinning(x=1, y=1)

        return ImagerRoi(
            x=(self.center_x - int(self.width / 2)) * binning.x,
            y=(self.center_y - int(self.height / 2)) * binning.y,
            width=self.width * binning.x,
            height=self.height * binning.y,
        )

    def __repr__(self) -> str:
        return f"UnitRoi(center_x={self.center_x},center_y={self.center_y},width={self.width},height={self.height})"

class SkyRoi(BaseModel):
    """Configuration for the region of interest (ROI) in the sky image."""
    sky_x: int
    sky_y: int
    width: int
    height: int

class SpecRoi(BaseModel):
    """Configuration for the region of interest (ROI) in the spectrograph."""
    width: int
    height: int
    fiber_x: int
    fiber_y: int
