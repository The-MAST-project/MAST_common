from typing import Literal

from pydantic import BaseModel, model_validator

from common.models.greateyes import Gain

from .network import NetworkConfig
from .power import OutletConfig
from .shutter import ShutterConfig


class GreateyesTemperatureConfig(BaseModel):
    """Configuration for Greateyes temperature settings."""

    # int, not float: greateyes' TemperatureControl_SetTemperature passes the value straight
    # to ctypes.c_int, so a whole degree is the only thing the hardware can be told. A float
    # here is not a finer set point, it is a TypeError at the SDK call.
    #
    # These were float, and MAST_spec never noticed because it read the temperature through
    # GreateyesSettingsModel -- whose target_cool is an int -- so building that model from
    # this config quietly rounded on the way past. When MAST_spec#54 collapsed the two
    # settings objects into this one, the rounding went with it and every deepspec camera
    # raised `TypeError: 'float' object cannot be interpreted as an integer` during
    # cool_down(), which runs from startup(): the spec service could not start at all.
    #
    # Stating it here rather than casting at the call site puts the constraint where the
    # value is defined. Pydantic accepts an integral float from the database (-5.0 -> -5) and
    # rejects a fractional one, so a set point the hardware cannot honour now fails when the
    # config loads instead of when a camera tries to cool.
    target_cool: int = -5  # Default target temperature in Celsius
    target_warm: int = 0  # Temperature to warm up to, in Celsius
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
    # Literal[2, 3, 4], matching GreateyesSettingsModel and the SDK header's
    # `bytesPerPixel [2 .. 4]`. A plain int here let a `sites` document carry a depth the
    # camera refuses -- `SetBitDepth(1) (status: one ore more parameters are out of range
    # (8))` -- which is the failure MAST_spec#54 fixed on the model side. The model is no
    # longer the gate: MAST_spec reads this config directly now, so the constraint has to be
    # on both or it is on neither.
    bytes_per_pixel: Literal[2, 3, 4] = 4
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
