from pydantic import BaseModel
from typing import Literal, Optional
from common.models.newton import NewtonCameraModel
from common.models.calibration import CalibrationModel

class HighspecModel(BaseModel):
    instrument: Literal['highspec']
    exposure: float
    number_of_exposures: Optional[int] = 1
    camera: Optional[NewtonCameraModel]
    # calibration: Optional[CalibrationModel]
