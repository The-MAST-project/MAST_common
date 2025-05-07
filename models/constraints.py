from pydantic import BaseModel
from typing import Optional
import datetime


class MoonConstraintModel(BaseModel):
    max_phase: Optional[float] = None
    min_distance: Optional[float] = None


class AirmassConstraintModel(BaseModel):
    max: Optional[float] = None


class SeeingConstraintModel(BaseModel):
    max: Optional[float] = None


class TimeWindowModel(BaseModel):
    start: Optional[datetime.datetime]
    end: Optional[datetime.datetime]


class ConstraintsModel(BaseModel):
    moon: Optional[MoonConstraintModel] = None
    airmass: Optional[AirmassConstraintModel] = None
    seeing: Optional[SeeingConstraintModel] = None
    time_window: Optional[TimeWindowModel] = None
