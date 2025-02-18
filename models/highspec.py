from pydantic import BaseModel
from typing import Literal, Optional

from common.models.newton import NewtonCameraSettingsModel

class HighspecModel(BaseModel):
    instrument: Literal['highspec']
    disperser: Literal
    camera: Optional[NewtonCameraSettingsModel]
