from typing import Literal

from pydantic import BaseModel, model_validator

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


class GreateyesGainConfig(BaseModel):
    """The sensor's gain setting, as ge.SetupGain takes it.

    0 -> Low (max. dynamic range), 1 -> Std (high sensitivity), per the SDK header.
    Mirrors GreateyesSettingsModel.gain so an exposure can override the site's choice,
    and both spell it the same way.
    """

    gain: Literal[0, 1] = 0


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
    # Optional, unlike its neighbours: no `sites` document carries a gain today, and a
    # required field would fail every camera's config load. While it is absent nothing
    # applies a gain, which is exactly the behaviour before this field existed. Set it and
    # every exposure applies it, unless the exposure names its own.
    gain: GreateyesGainConfig | None = None

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
