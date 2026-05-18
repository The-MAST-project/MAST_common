from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from common.config.power import PowerSwitchConfig
from common.config.shutter import ShutterConfig

NewtonAmplifierMode = Literal["em", "conventional"]


class NewtonRoiModel(BaseModel):
    hstart: int | None = None
    hend: int | None = None
    vstart: int | None = None
    vend: int | None = None


class CoolerMode(Enum):
    RETURN_TO_AMBIENT = 0
    MAINTAIN_CURRENT_TEMP = 1


class NewtonTemperatureConfig(BaseModel):
    """Configuration for the Newton camera temperature settings."""

    regular_set_point: int = -10  # Default target temperature in Celsius
    science_set_point: int = -85  # Target temperature for science exposures
    cooler_mode: int = CoolerMode.RETURN_TO_AMBIENT.value  # Default cooler mode


class NewtonBinning(BaseModel):
    x: int = Field(1, ge=1, description="Binning factor in X")
    y: int = Field(1, ge=1, description="Binning factor in Y")


class NewtonRoi(BaseModel):
    hstart: int | None = None
    hend: int | None = None
    vstart: int | None = None
    vend: int | None = None


class NewtonSettingsConfig(BaseModel):
    """Configuration for the Newton camera settings."""

    binning: NewtonBinning | None = Field(
        default_factory=lambda: NewtonBinning(x=1, y=1)
    )  # Binning configuration for the camera
    roi: NewtonRoi | None = None  # Region of interest settings
    shutter: ShutterConfig | None = None
    acquisition_mode: int = 1  # Default acquisition mode
    number_of_exposures: int = 1
    exposure_duration: float = 5.0  # Default exposure duration in seconds
    amplifier_mode: NewtonAmplifierMode = "conventional"  # Default amplifier mode
    em_gain: int = 254  # Default EM gain value
    pre_amp_gain: int = 0  # Default pre-amplifier gain value
    temperature: NewtonTemperatureConfig | None = None
    read_mode: int | None = None
    camera_enabled: bool = True

    @model_validator(mode="after")
    def validate_newton_settings(self):
        if self.binning is None:
            self.binning = NewtonBinning(x=1, y=1)
        return self


class HighspecConfig(BaseModel):
    power: PowerSwitchConfig
    settings: NewtonSettingsConfig
    camera: Literal["qhy600", "newton"]
    valid_cameras: list[str]
    camera_enabled: bool = True
    known_as_good_focus_position: int
