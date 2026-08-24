from enum import IntEnum, StrEnum
from typing import Literal

from pydantic import BaseModel, field_validator

from common.spec import FrameType


class Gain(StrEnum):
    """The sensor's gain, as the database and the API name it.

    A StrEnum, so a document carries "low"/"high" and OpenAPI renders those as the options
    rather than the SDK's 0/1. MAST_spec maps them to the integers SetupGain takes, next to
    the vendor comment that documents the pairing:

        0 -> Low ( Max. Dyn. Range )
        1 -> Std ( High Sensitivity )

    Not an IntEnum, for a second reason: `low` would be 0, and `gain or conf.gain` -- the
    fallback both callers use -- treats 0 as unset, so asking explicitly for low gain would
    silently get the configured value instead.
    """

    low = "low"
    high = "high"


class BinningModel(BaseModel):
    x: int = 1
    y: int = 1


class ReadoutAmplifiers(IntEnum):
    OSR = 0
    OSL = 1
    OSR_AND_OSL = 2


ReadoutAmplifiersNames = Literal["OSR", "OSL", "OSR_AND_OSL"]
ReadoutAmplifiersMapping = {
    "OSR": ReadoutAmplifiers.OSR,
    "OSL": ReadoutAmplifiers.OSL,
    "OSR_AND_OSL": ReadoutAmplifiers.OSR_AND_OSL,
}
readout_amplifier_names = {
    ReadoutAmplifiers.OSR: "OSR",
    ReadoutAmplifiers.OSL: "OSL",
    ReadoutAmplifiers.OSR_AND_OSL: "OSR_AND_OSL",
}


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


ReadoutSpeedNames = Literal[
    "50_kHz",
    "100_kHz",
    "250_kHz",
    "500_kHz",
    "1_MHz",
    "3_MHz",
]
ReadoutSpeedMapping = {
    "50_kHz": ReadoutSpeed.ReadoutSpeed_50_kHz,
    "100_kHz": ReadoutSpeed.ReadoutSpeed_100_kHz,
    "250_kHz": ReadoutSpeed.ReadoutSpeed_250_kHz,
    "500_kHz": ReadoutSpeed.ReadoutSpeed_500_kHz,
    "1_MHz": ReadoutSpeed.ReadoutSpeed_1_MHz,
    "3_MHz": ReadoutSpeed.ReadoutSpeed_3_MHz,
}


class ReadoutModel(BaseModel):
    mode: ReadoutAmplifiers
    speed: ReadoutSpeed

    # `cls` and the explicit @classmethod are load-bearing, not style: pydantic raises
    # `PydanticUserError: @field_validator cannot be applied to instance methods` at CLASS
    # DEFINITION time, so a first parameter named `self` breaks every import of this module
    # -- and with it the whole spec service. ruff's N805 ("first argument should be named
    # self") fires on validators written the correct way; the @classmethod stops it asking.
    @field_validator("mode")
    @classmethod
    def readout_validator(cls, value):
        return value


class ProbingModel(BaseModel):
    interval: float | None = None
    boot_delay: float | None = None


class GreateyesSettingsModel(BaseModel):
    enabled: bool | None = True
    binning: BinningModel | None = None
    # 1 is not a value the hardware accepts. The greateyes SDK's own header says
    # `bytesPerPixel [2 .. 4]` ("for cameras with 16 bit adc bytesPerPixel is always 2"),
    # and MAST_spec's BytesPerPixel enum likewise knows only Two, Three and Four.
    #
    # It was permitted here, and MAST_spec's manual deepspec/expose endpoint duly sent 1 --
    # dormant for as long as nothing in that path applied it, then `SetBitDepth(1, addr=2)
    # (status: one ore more parameters are out of range (8))` the moment something did. A
    # value the SDK refuses belongs nowhere in this model: rejecting it here turns that into
    # a validation error at the API boundary instead of a hardware failure three layers in.
    #
    # Every deepspec camera in the ns config carries 4, so nothing in service is narrowed
    # out by this.
    bytes_per_pixel: Literal[2, 3, 4] | None = None
    gain: Gain | None = None
    readout: ReadoutModel | None = None
    temp: TemperatureSettingsModel | None = None
    crop: CropModeModel | None = None
    shutter: ShutterModel | None = None
    probing: ProbingModel | None = None
    exposure_duration: float | None = None
    number_of_exposures: int | None = 1
    image_file: str | None = None
    frame_type: FrameType = FrameType.LIGHT
