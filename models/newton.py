from enum import Enum, StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from common.config.power import PowerSwitchConfig
from common.config.shutter import ShutterConfig

NewtonAmplifierMode = Literal["em", "conventional"]


class NewtonHSSpeed(StrEnum):
    """The horizontal shift speeds the Newton offers, as the config and the API name them.

    Here rather than in MAST_spec so the database, the config model and the endpoint all
    validate against one definition -- the same reason NewtonAmplifierMode lives here.
    MAST_spec maps these to the SDK's speed indices (0, 1, 2), which is the order the
    camera reports them in: GetHSSpeed says [3.0, 1.0, 0.05] MHz for both amplifiers.

    Being a StrEnum, the VALUE is what a document must carry -- "0.05 MHz", not the
    member name.
    """

    MHz_3_0 = "3.0 MHz"
    MHz_1_0 = "1.0 MHz"
    MHz_0_05 = "0.05 MHz"


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
    """A region of the sensor, in the SDK's 1-based inclusive pixel coordinates.

    All four bounds are required: a partial region is not a thing the camera can be asked
    for, and `SetImage` cannot take None. To use the whole sensor, leave `roi` out of the
    settings entirely -- see NewtonSettingsConfig.roi.

    There is no -1 sentinel any more. It meant "to the edge", and MAST_spec resolved it by
    writing the detector size back into this object -- the shared config instance -- so the
    first exposure fixed the region for every later one.
    """

    hstart: int = Field(ge=1, description="First column, 1-based inclusive")
    hend: int = Field(ge=1, description="Last column, 1-based inclusive")
    vstart: int = Field(ge=1, description="First row, 1-based inclusive")
    vend: int = Field(ge=1, description="Last row, 1-based inclusive")


class NewtonSettingsConfig(BaseModel):
    """Configuration for the Newton camera settings."""

    binning: NewtonBinning | None = Field(
        default_factory=lambda: NewtonBinning(x=1, y=1)
    )  # Binning configuration for the camera
    # None means the full sensor -- and MAST_spec applies that explicitly rather than
    # skipping SetImage, which is what left the geometry and the binning at whatever the
    # previous exposure had set.
    roi: NewtonRoi | None = None
    shutter: ShutterConfig | None = None
    acquisition_mode: int = 1  # Default acquisition mode
    number_of_exposures: int = 1
    exposure_duration: float = 5.0  # Default exposure duration in seconds
    amplifier_mode: NewtonAmplifierMode = "conventional"  # Default amplifier mode
    em_gain: int = 254  # Default EM gain value
    pre_amp_gain: int = 0  # Default pre-amplifier gain value
    # The slowest speed, which is what every exposure has used. It was hardcoded in
    # MAST_spec -- the one camera setting with nowhere to be configured -- so this is the
    # value that was already in force, now stated somewhere it can be changed.
    horizontal_shift_speed: NewtonHSSpeed = NewtonHSSpeed.MHz_0_05
    temperature: NewtonTemperatureConfig | None = None
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
