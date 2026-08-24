from pydantic import BaseModel, model_validator

from common.models.greateyes import Gain

from .network import NetworkConfig
from .power import OutletConfig
from .shutter import ShutterConfig


class GreateyesTemperatureConfig(BaseModel):
    """Configuration for Greateyes temperature settings."""

    target_cool: float = -5.0  # Default target temperature in Celsius
    target_warm: float = 0.0  # Temperature hysteresis in Celsius
    check_interval: int = 30  # Interval to check temperature in seconds


class GreateyesCropConfig(BaseModel):
    col: int = 1056
    line: int = 1027
    enabled: bool = False


class GreateyesReadoutConfig(BaseModel):
    speed: int
    mode: int = 2


class GreateyesProbingConfig(BaseModel):
    boot_delay: int = 25  # seconds to wait after booting the camera
    interval: int = 60  # seconds to check the camera status


class GreateyesBinningConfig(BaseModel):
    x: int
    y: int


class GreateyesSettingConfig(BaseModel):
    """Configuration for Greateyes settings."""

    binning: GreateyesBinningConfig | None = None  # Binning configuration for the camera
    bytes_per_pixel: int = 4  # Default bytes per pixel for Greateyes camera
    number_of_exposures: int = 1
    exposure_duration: float = 5.0  # Default exposure duration in seconds
    temp: GreateyesTemperatureConfig
    crop: GreateyesCropConfig
    shutter: ShutterConfig
    readout: GreateyesReadoutConfig
    probing: GreateyesProbingConfig
    # The same Gain the exposure settings and the endpoint use -- one type from the document
    # to the SDK call, so `gain or conf.gain` in MAST_spec compares like with like. It was a
    # nested object holding the SDK's 0/1, which meant the fallback put the wrong type into
    # a field expecting the enum: a ValidationError the first time a site set one.
    #
    # Optional, unlike its neighbours: no `sites` document carries a gain today, and a
    # required field would fail every camera's config load. While it is absent nothing
    # applies a gain, which is exactly the behaviour before this field existed. Set it and
    # every exposure applies it, unless the exposure names its own.
    gain: Gain | None = None

    @model_validator(mode="after")
    def validate_greateyes_setting(self):
        if self.binning is None:
            self.binning = GreateyesBinningConfig(x=1, y=1)
        return self


class GreateyesConfig(BaseModel):
    network: NetworkConfig | None = None  # Network configuration for Greateyes device
    power: OutletConfig | None = None  # Power switch configuration
    enabled: bool | None = True
    device: int | None = None  # Device number
    settings: GreateyesSettingConfig | None = None  # Camera settings
