from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, field_validator


class Gain(IntEnum):
    Low = 0  # Low ( Max. Dyn. Range )
    High = 1  # Std ( High Sensitivity )


class GainSettingModel(BaseModel):
    gain: Gain


class BinningModel(BaseModel):
    x: int = 1
    y: int = 1


class ReadoutAmplifiers(IntEnum):
    OSR = 0
    OSL = 1
    OSR_AND_OSL = 2


class CropModeModel(BaseModel):
    col: int
    line: int
    enabled: bool


class TemperatureSettingsModel(BaseModel):
    target_cool: int  # [centigrade] target when cooling down
    target_warm: int  # [centigrade] target when warming up
    check_interval: float  # [seconds] to check backside temperature


class ShutterModel(BaseModel):
    automatic: bool
    close_time: int
    open_time: int


class ReadoutSpeed(IntEnum):
    ReadoutSpeed_50_kHz = 50
    ReadoutSpeed_100_kHz = 100
    ReadoutSpeed_250_kHz = 250
    ReadoutSpeed_500_kHz = 500
    ReadoutSpeed_1_MHz = 1000
    ReadoutSpeed_3_MHz = 3000


class ReadoutModel(BaseModel):
    mode: ReadoutAmplifiers
    speed: ReadoutSpeed

    @field_validator("mode")
    def readout_validator(cls, value):
        return value


class ProbingModel(BaseModel):
    interval:   float | None = None
    boot_delay:   float | None = None


class GreateyesSettingsModel(BaseModel):
    enabled: bool | None = True
    binning: BinningModel | None = None
    bytes_per_pixel: Literal[1, 2, 3, 4] | None = 2
    temp: TemperatureSettingsModel | None = None
    crop: CropModeModel | None = None
    shutter: ShutterModel | None = None
    readout: ReadoutModel | None = None
    probing: ProbingModel | None = None
    exposure_duration:   float | None = None
    number_of_exposures:   int | None = 1
    image_file:   str | None = None
