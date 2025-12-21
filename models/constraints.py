import datetime

from pydantic import BaseModel


class MoonConstraintModel(BaseModel):
    max_phase: float | None = None
    min_distance: float | None = None


class AirmassConstraintModel(BaseModel):
    max: float | None = None


class SeeingConstraintModel(BaseModel):
    max: float | None = None


class TimeWindowModel(BaseModel):
    start: datetime.datetime | None
    end: datetime.datetime | None


class ConstraintsModel(BaseModel):
    moon: MoonConstraintModel | None = None
    airmass: AirmassConstraintModel | None = None
    seeing: SeeingConstraintModel | None = None
    time_window: TimeWindowModel | None = None
