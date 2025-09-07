from pydantic import BaseModel, model_validator

from .imager import ImagerBinningConfig
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


class GreateyesSettingConfig(BaseModel):
    """Configuration for Greateyes settings."""

    binning: ImagerBinningConfig | None = None  # Binning configuration for the camera
    bytes_per_pixel: int = 4  # Default bytes per pixel for Greateyes camera
    number_of_exposures: int = 1
    exposure_duration: float = 5.0  # Default exposure duration in seconds
    temp: GreateyesTemperatureConfig
    crop: GreateyesCropConfig
    shutter: ShutterConfig
    readout: GreateyesReadoutConfig
    probing: GreateyesProbingConfig

    @model_validator(mode="after")
    def validate_greateyes_setting(self):
        if self.binning is None:
            self.binning = ImagerBinningConfig(x=1, y=1)
        return self


class GreateyesConfig(BaseModel):
    network: NetworkConfig | None = None  # Network configuration for Greateyes device
    power: OutletConfig | None = None  # Power switch configuration
    enabled: bool | None = True
    device: int | None = None  # Device number
    settings: GreateyesSettingConfig | None = None  # Camera settings
