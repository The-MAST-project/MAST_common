from pydantic import BaseModel
from typing import Literal, Optional
from common.models.newton import NewtonCameraModel

class HighspecModel(BaseModel):
    instrument: Literal['highspec']
    exposure: float
    number_of_exposures: Optional[int] = 1
    camera: Optional[NewtonCameraModel]
