import logging

from pydantic import BaseModel, model_validator

from common.asi import ASI_294MM_SUPPORTED_BINNINGS_LITERAL

from .calibration import CalibrationConfig
from .covers import CoversConfig
from .focuser import FocuserConfig
from .imager import ImagerConfig
from .mount import MountConfig
from .phd2 import PHD2Config
from .power import PowerSwitchConfig
from .rois import RoisConfig
from .stage import StageConfig


class ToleranceConfig(BaseModel):
    """Configuration for the acquisition tolerances."""

    ra_arcsec: float
    dec_arcsec: float


class AcquisitionConfig(BaseModel):
    """Configuration for the acquisition settings."""

    exposure: float
    binning: ASI_294MM_SUPPORTED_BINNINGS_LITERAL
    tolerance: ToleranceConfig
    tries: int
    gain: int
    rois: RoisConfig


class GuidingConfig(BaseModel):
    """Configuration for guiding settings."""

    exposure: float
    binning: ASI_294MM_SUPPORTED_BINNINGS_LITERAL
    tolerance: ToleranceConfig
    gain: int
    min_ra_correction_arcsec: float
    min_dec_correction_arcsec: float
    cadence_seconds: int
    rois: RoisConfig


class GuiderConfig(BaseModel):
    method: str
    valid_methods: list[str]


class SolvingConfig(BaseModel):
    method: str
    valid_methods: list[str]


class AutofocusConfig(BaseModel):
    """Configuration for autofocus settings."""

    exposure: float
    binning: ASI_294MM_SUPPORTED_BINNINGS_LITERAL
    rois: RoisConfig | None = None
    images: int
    spacing: int
    max_tolerance: int
    max_tries: int


class UnitConfig(BaseModel):
    """Complete configuration for a MAST unit.

    This is the top-level configuration class that contains all settings
    needed to configure and operate a MAST unit.
    """

    name: str
    power_switch: PowerSwitchConfig
    imager: ImagerConfig
    stage: StageConfig
    focuser: FocuserConfig
    covers: CoversConfig
    mount: MountConfig
    phd2: PHD2Config
    acquisition: AcquisitionConfig
    solving: SolvingConfig
    guiding: GuidingConfig
    autofocus: AutofocusConfig
    guider: GuiderConfig
    calibration: CalibrationConfig | None = None

    @model_validator(mode="after")
    def validate_unit_config(self):
        # conditions = [self.guider.method == "phd2", self.imager.imager_type == "phd2"]

        # if any(conditions) and not all(conditions):
        #     raise ValueError(
        #         f"if any of {self.imager.imager_type=} or {self.guider.method=} is 'phd2', then BOTH must be 'phd2'"
        #     )
        logger = logging.getLogger("mast.config.unit")
        logger.debug(
            f"Validated UnitConfig for unit '{self.name}', focuser: '{self.focuser}'"
        )
        return self

    def focus_seed_position(self) -> int | None:
        """Best available focuser seed position, or ``None`` if none exists.

        Prefers the calibration record ``calibration.focuser.best_position``
        (provenance-carrying, written by the most recent successful autofocus)
        over the bare operational ``focuser.known_as_good_position``.  A
        ``known_as_good_position`` of 0 (the unset default) is treated as absent.
        Autofocus seeds Phase 0 from this; ``None`` means no prior focus exists,
        so fall through to a full acquisition sweep.
        """
        if self.calibration is not None and self.calibration.focuser is not None:
            return self.calibration.focuser.best_position
        if self.focuser.known_as_good_position:  # 0 == unset default
            return self.focuser.known_as_good_position
        return None
