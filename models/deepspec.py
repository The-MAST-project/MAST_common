from pydantic import BaseModel
from typing import Literal, TypedDict, Optional, Dict
from common.models.greateyes import GreateyesSettingsModel
from common.models.calibration import CalibrationModel

class DeepspecModel(BaseModel):
    instrument: Literal['deepspec']
    camera: Optional[Dict[str, GreateyesSettingsModel] | GreateyesSettingsModel] = None
    # calibration: Optional[CalibrationModel]
