from typing import Optional, List
from astropy.coordinates import Angle


class SolvingSolution:
    ra_rads: float
    dec_rads: float
    ra_hours: float
    dec_degs: float
    matched_stars: int = 0
    catalog_stars: int = 0
    rotation_angle_degs: float
    pixel_scale: float


class SolvingResult:
    succeeded: bool
    errors: Optional[List[str]] = None
    solution: SolvingSolution
    result = None
    elapsed_seconds: float


class SolvingTolerance:
    ra: Angle
    dec: Angle

    def __init__(self, ra: Angle, dec: Angle):
        self.ra = ra
        self.dec = dec