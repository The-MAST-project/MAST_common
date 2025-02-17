from pydantic import BaseModel
from typing import Literal, Optional
from common.models.newton import NewtonCameraModel

class HighspecModel(BaseModel):
    instrument: Literal['highspec']
    camera: Optional[NewtonCameraModel]
