from enum import IntFlag, auto
from typing import Literal

from astropy.coordinates import Angle
from pydantic import BaseModel


class SolverId(IntFlag):
    PlaneWaveCli = auto()
    PlaneWaveShm = auto()
    AstrometryDotNet = auto()
    MastrometryDotNet = auto()
    # Astap = auto()


SolverIdNames = Literal["PlaneWaveCli", "PlaneWaveShm", "AstrometryDotNet", "MastrometryDotNet"]


class SolvingSolution(BaseModel):
    ra_rads: float | None = None
    dec_rads: float | None = None
    ra_hours: float | None = None
    dec_degs: float | None = None
    matched_stars: int | None = None
    catalog_stars: int | None = None
    rotation_angle_degs: float | None = None
    pixel_scale: float | None = None

    #: The WCS CD matrix of the solved frame, degrees per pixel, straight from its FITS
    #: header. Kept because `rotation_angle_degs` alone cannot convert a pixel offset to a
    #: sky offset: it says how the field is turned but not whether it is MIRRORED, and a
    #: wrong parity flips the RA sign of every derived offset while leaving the magnitude
    #: right -- which is the hardest kind of error to notice. The CD matrix carries
    #: rotation, scale and parity together.
    #:
    #: Per DOWNSAMPLED pixel where the backend downsamples before solving.
    cd1_1: float | None = None
    cd1_2: float | None = None
    cd2_1: float | None = None
    cd2_2: float | None = None


class SolvingResult(BaseModel):
    succeeded: bool | None = None
    errors: list[str] | None = None
    solution: SolvingSolution | None = None
    solver_result: dict | None = None
    elapsed_seconds: float | None = None


class SolvingTolerance:
    def __init__(self, ra, dec) -> None:
        self.ra: Angle = ra
        self.dec: Angle = dec
