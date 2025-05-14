from typing import Literal, Optional

from pydantic import BaseModel


class BinningModel(BaseModel):
    x: Optional[Literal[1, 2, 3, 4]]
    y: Optional[Literal[1, 2, 3, 4]]


class RoiModel(BaseModel):
    x: Optional[int]
    y: Optional[int]
    width: Optional[int]
    height: Optional[int]


class SettingsModel(BaseModel):
    binning: Optional[BinningModel]
    roi: Optional[RoiModel]
    set_point: Optional[float]
    exposure: Optional[float]
    gain: Optional[int]
