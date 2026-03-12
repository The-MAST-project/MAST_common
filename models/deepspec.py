from typing import Dict, Optional

from pydantic import BaseModel

from common.models.greateyes import GreateyesSettingsModel


class DeepspecSettings(BaseModel):
    camera: Optional[Dict[str, GreateyesSettingsModel]] = None
