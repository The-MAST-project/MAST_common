from pydantic import BaseModel
from typing import Optional

class MoonConstraintModel(BaseModel):
    max_phase: float
    min_distance: float

class AirmassConstraintModel(BaseModel):
    max: float

class SeeingConstraintModel(BaseModel):
    max: float

class ConstraintsModel(BaseModel):
    moon: Optional[MoonConstraintModel]
    airmass: Optional[AirmassConstraintModel]
    seeing: Optional[SeeingConstraintModel]
