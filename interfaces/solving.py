from abc import ABC, abstractmethod
from enum import IntFlag, StrEnum, auto
from typing import Literal

from astropy.coordinates import Angle
from pydantic import BaseModel

from common.const import Const


class SolverId(IntFlag):
    PlaneWaveCli = auto()
    PlaneWaveShm = auto()
    AstrometryDotNet = auto()
    MastrometryDotNet = auto()
    # Astap = auto()


SolverIdNames = Literal["PlaneWaveCli", "PlaneWaveShm", "AstrometryDotNet", "MastrometryDotNet"]


class SolvingConfidenceLevel(StrEnum):
    Unsolved = "unsolved"
    Marginal = "marginal"
    Confident = "confident"
    VeryConfident = "very confident"
    Certain = "certain"


#: Sky-coordinate conversions. Named because they are the kind of constant that reads
#: as obviously-correct at every call site and is therefore never checked: 15 is
#: degrees per hour of right ascension, not a count of anything.
ARCSEC_PER_DEGREE: float = 3600.0
DEGREES_PER_RA_HOUR: float = 15.0
DEGREES_PER_TURN: float = 360.0
HALF_TURN_DEGREES: float = 180.0


class SolvingSolution(BaseModel):
    ra_rads: float | None = None
    dec_rads: float | None = None
    ra_hours: float = 0.0
    ra_degs: float = 0.0
    dec_degs: float = 0.0
    matched_stars: int = 0
    catalog_stars: int = 0
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

    sources: int | None = None
    index_file: str | None = None
    confidence_level: SolvingConfidenceLevel | None = None
    confidence: float | None = None


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
            "native_result": (self.native_result.to_dict() if self.native_result else None),
        }


class SolvingTolerance:
    ra: Angle
    dec: Angle

    def __init__(self, ra: Angle, dec: Angle):
        self.ra = ra
        self.dec = dec


class SolverInterface(ABC):
    @abstractmethod
    def solve(self, unit, settings, target, phase: Const.SolvingPhase) -> SolvingResult:
        pass

    @abstractmethod
    def solve_and_correct(self):
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass


def target_offset_arcsec(target, solution: "SolvingSolution") -> tuple[float, float]:
    """How far `target` lies from where `solution` says the telescope is pointing.

    Returns ``(d_ra_arcsec, d_dec_arcsec)``, each **target minus solved**, so the
    pair is the correction to apply rather than the error observed.

    The right-ascension term is wrapped through half a turn so a target either
    side of 0h subtracts to the short way round rather than to nearly a full
    turn. It carries **no** ``cos(dec)`` factor: a mount offset command wants
    plain RA, and a caller converting to a tangent-plane quantity -- pixels
    through a CD matrix, say -- has to apply that factor itself.

    Shared because two callers must agree on what "off target" means: the
    acquisition loop that corrects the mount, and the lock nudge that re-references
    guiding to the same target just before the fold mirror hides it. Two copies of
    this arithmetic would let them disagree silently.
    """
    d_ra_deg = (target.ra.deg - solution.ra_hours * DEGREES_PER_RA_HOUR) % DEGREES_PER_TURN
    if d_ra_deg > HALF_TURN_DEGREES:
        d_ra_deg -= DEGREES_PER_TURN
    d_dec_arcsec = target.dec.arcsecond - Angle(solution.dec_rads, unit="rad").arcsecond
    return d_ra_deg * ARCSEC_PER_DEGREE, d_dec_arcsec
