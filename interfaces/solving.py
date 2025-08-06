from abc import ABC, abstractmethod

from astropy.coordinates import Angle
from pydantic import BaseModel


class SolvingSolution(BaseModel):
    ra_rads: float | None = None
    dec_rads: float | None = None
    ra_hours: float = 0.0
    dec_degs: float = 0.0
    matched_stars: int = 0
    catalog_stars: int = 0
    rotation_angle_degs: float | None = None
    pixel_scale: float | None = None
    sources: int | None = None
    index_file: str | None = None


class SolvingResult:

    succeeded: bool
    errors: list[str] | None = None
    solution: SolvingSolution | None
    native_result = None

    def __init__(
        self,
        succeeded: bool,
        errors: list[str] | None = None,
        solution: SolvingSolution | None = None,
        native_result=None,
    ):
        self.succeeded: bool = succeeded
        self.errors = errors
        self.solution: SolvingSolution | None = solution
        self.native_result = native_result

    def to_dict(self):
        return {
            "succeeded": self.succeeded,
            "errors": self.errors,
            "solution": self.solution.model_dump() if self.solution else None,
            "native_result": (
                self.native_result.to_dict() if self.native_result else None
            ),
        }


class SolvingTolerance:
    ra: Angle
    dec: Angle

    def __init__(self, ra: Angle, dec: Angle):
        self.ra = ra
        self.dec = dec


class SolverInterface(ABC):

    @abstractmethod
    def solve(self, unit, settings, target) -> SolvingResult:
        pass

    @abstractmethod
    def solve_and_correct(self):
        pass
