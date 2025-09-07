from pydantic import BaseModel, Field, model_validator

from cameras.andor.newton import CoolerMode

from .shutter import ShutterConfig


class NewtonTemperatureConfig(BaseModel):
    """Configuration for the Newton camera temperature settings."""

    set_point: int = -10  # Default target temperature in Celsius
    cooler_mode: CoolerMode = CoolerMode.RETURN_TO_AMBIENT


class NewtonBinning(BaseModel):
    x: int = Field(1, ge=1, description="Binning factor in X")
    y: int = Field(1, ge=1, description="Binning factor in Y")


class NewtonSettingsConfig(BaseModel):
    """Configuration for the Newton camera settings."""

    binning: NewtonBinning | None = Field(
        default_factory=lambda: NewtonBinning(x=1, y=1)
    )  # Binning configuration for the camera
    shutter: ShutterConfig | None = None
    acquisition_mode: int = 1  # Default acquisition mode
    number_of_exposures: int = 1
    exposure_duration: float = 5.0  # Default exposure duration in seconds
    em_gain: int = 254  # Default EM gain value
    pre_amp_gain: int = 0  # Default pre-amplifier gain value
    temperature: NewtonTemperatureConfig | None = None
    read_mode: int | None = None

    @model_validator(mode="after")
    def validate_newton_settings(self):
        if self.binning is None:
            self.binning = NewtonBinning(x=1, y=1)
        return self
