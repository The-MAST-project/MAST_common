from pydantic import BaseModel
from typing import Literal, TypedDict, Optional, Dict
from common.models.greateyes import GreateyesSettingsModel
from common.models.calibration import CalibrationModel

class DeepspecModel(BaseModel):
    instrument: Literal['deepspec']
    exposure: float
    number_of_exposures: Optional[int] = 1
    camera: Optional[Dict[str, GreateyesSettingsModel] | GreateyesSettingsModel] = None
    # calibration: Optional[CalibrationModel]
