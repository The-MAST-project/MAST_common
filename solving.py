from typing import Optional, List
from astropy.coordinates import Angle


class SolvingSolution:
    ra_rads: Optional[float] = None
    dec_rads: Optional[float] = None
    ra_hours: Optional[float] = None
    dec_degs: Optional[float] = None
    matched_stars: Optional[int] = None
    catalog_stars: Optional[int] = None
    rotation_angle_degs: Optional[float] = None
    pixel_scale: Optional[float] = None

    def to_dict(self):
        return self.__dict__


class SolvingResult:
    succeeded: bool | None = None
    errors: Optional[List[str]] = None
    solution: SolvingSolution | None = None
    solver_result = None
    elapsed_seconds: float | None = None

    def __init__(self, succeeded: Optional[bool] = None,
                 errors: Optional[List[str]] = None,
                 solution: Optional[SolvingSolution] = None,
                 solver_result = None):
        self.succeeded = succeeded
        self.errors = errors
        self.solution = solution
        self.solver_result = solver_result

    def to_dict(self):
        ret = {
            'succeeded': self.succeeded,
            'errors': self.errors,
            'solution': self.solution.to_dict(),
            'elapsed_seconds': self.elapsed_seconds,
        }
        if self.solver_result and hasattr(self.solver_result, 'to_dict'):
            ret['solving_result'] = self.solver_result.to_dict()

        return ret


class SolvingTolerance:
    ra: Angle
    dec: Angle

    def __init__(self, ra: Angle, dec: Angle):
        self.ra = ra
        self.dec = dec