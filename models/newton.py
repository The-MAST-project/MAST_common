from typing import Literal, Optional

from pydantic import BaseModel

from common.config.power import PowerSwitchConfig


class NewtonRoiModel(BaseModel):
    hstart: Optional[int]
    hend: Optional[int]
    vstart: Optional[int]
    vend: Optional[int]


class NewtonTemperatureModel(BaseModel):
    set_point: Optional[int]
    cooler_mode: Optional[int]


class NewtonShutterModel(BaseModel):
    opening_time: Optional[int]
    closing_time: Optional[int]


class NewtonBinningModel(BaseModel):
    x: Optional[int]
    y: Optional[int]


class NewtonCameraSettingsModel(BaseModel):
    binning: Optional[NewtonBinningModel]
    roi: Optional[NewtonRoiModel]
    temperature: Optional[NewtonTemperatureModel]
    shutter: Optional[NewtonShutterModel]
    acquisition_mode: Optional[Literal[0, 1]]
    em_gain: Optional[int]
    pre_amp_gain: Optional[Literal[0, 1, 2]]
    exposure_duration: Optional[float]
    number_of_exposures: Optional[int] = 1


class HighspecConfig(BaseModel):
    power: PowerSwitchConfig
    settings: NewtonCameraSettingsModel
    camera: Literal["qhy600", "newton"]
    valid_cameras: list[str]
    known_as_good_focus_position: int
