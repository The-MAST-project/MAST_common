from typing import Literal

from pydantic import BaseModel


class BinningModel(BaseModel):
    x: Literal[1, 2, 3, 4] | None
    y: Literal[1, 2, 3, 4] | None


class RoiModel(BaseModel):
    x: int | None
    y: int | None
    width: int | None
    height: int | None


class SettingsModel(BaseModel):
    binning: BinningModel | None
    roi: RoiModel | None
    set_point: float | None
    exposure: float | None
    gain: int | None
