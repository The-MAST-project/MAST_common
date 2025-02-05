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
    opening_time: Optional[float]
    closing_time: Optional[float]

class NewtonBinningModel(BaseModel):
    x: Optional[int]
    y: Optional[int]

class NewtonCameraModel(BaseModel):
    binning: Optional[NewtonBinningModel]
    roi: Optional[NewtonRoiModel]
    temperature: Optional[NewtonTemperatureModel]
    shutter: Optional[NewtonShutterModel]
    acquisition_mode: Optional[Literal[0, 1]]
    gain: Optional[int]
