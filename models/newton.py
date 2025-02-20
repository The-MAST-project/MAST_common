from pydantic import BaseModel
from typing import Optional,Literal

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
