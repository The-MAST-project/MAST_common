from enum import IntEnum
from typing import Optional

from pydantic import BaseModel, model_validator, field_validator
from typing_extensions import Literal

class Gain(IntEnum):
    Low = 0,    # Low ( Max. Dyn. Range )
    High = 1,   # Std ( High Sensitivity )


class GainSettingModel(BaseModel):
    gain: Gain


class BinningModel(BaseModel):
    x: int = 1
    y: int = 1

class ReadoutAmplifiers(IntEnum):
    OSR = 0,
    OSL = 1,
    OSR_AND_OSL = 2,

class CropModeModel(BaseModel):
    col: int
    line: int
    enabled: bool

class TemperatureSettingsModel(BaseModel):
    target_cool: int            # [centigrade] target when cooling down
    target_warm: int            # [centigrade] target when warming up
    check_interval: float       # [seconds] to check backside temperature


class ShutterModel(BaseModel):
    automatic: bool
    close_time: int
    open_time: int


class ReadoutSpeed(IntEnum):
    ReadoutSpeed_50_kHz = int(50)
    ReadoutSpeed_100_kHz = int(100)
    ReadoutSpeed_250_kHz = int(250)
    ReadoutSpeed_500_kHz = int(500)
    ReadoutSpeed_1_MHz = int(1000)
    ReadoutSpeed_3_MHz = int(3000)


class ReadoutModel(BaseModel):
    mode: ReadoutAmplifiers
    speed: ReadoutSpeed

    @field_validator('mode')
    def readout_validator(cls, value):
        return value

class ProbingModel(BaseModel):
    interval: Optional[float]
    boot_delay: Optional[float]


class GreateyesSettingsModel(BaseModel):
    enabled: Optional[bool] = True
    binning: Optional[BinningModel]
    bytes_per_pixel: Optional[Literal[1, 2, 3, 4]]
    temp: Optional[TemperatureSettingsModel]
    crop: Optional[CropModeModel]
    shutter: Optional[ShutterModel]
    readout: Optional[ReadoutModel]
    probing: Optional[ProbingModel]
    exposure_duration: Optional[float]
    number_of_exposures: Optional[int] = 1

    # @model_validator(mode='after')
    # def validate_deepsepc_camera(cls, values):
    #     return values
