from pydantic import BaseModel
from typing import Literal, Optional

from common.models.newton import NewtonCameraSettingsModel
from common.spec import Disperser


class HighspecModel(BaseModel):
    instrument: Literal["highspec"]
    disperser: Disperser
    camera: Optional[NewtonCameraSettingsModel]
