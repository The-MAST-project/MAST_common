from datetime import date, datetime

from pydantic import BaseModel


class MoonConstraintModel(BaseModel):
    max_phase: float | None = None
    min_distance: float | None = None


class AirmassConstraintModel(BaseModel):
    max: float | None = None


class SeeingConstraintModel(BaseModel):
    max: float | None = None


class TimeWindow(BaseModel):
    start: date | datetime | None = None
    end: date | datetime | None = None
    ndays: int = 1


class ConstraintsModel(BaseModel):
    moon: MoonConstraintModel | None = None
    airmass: AirmassConstraintModel | None = None
    seeing: SeeingConstraintModel | None = None
    time_window: TimeWindow | None = None
