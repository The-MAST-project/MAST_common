from pydantic import BaseModel
from typing import Literal, Optional, Dict

from common.models.greateyes import GreateyesSettingsModel


class DeepspecModel(BaseModel):
    instrument: Literal["deepspec"]
    camera: Optional[Dict[str, GreateyesSettingsModel]] = None
