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
