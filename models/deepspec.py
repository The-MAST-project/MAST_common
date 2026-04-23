from typing import Dict, Optional

from pydantic import BaseModel

from .greateyes import GreateyesSettingsModel


class DeepspecSettings(BaseModel):
    camera: Optional[Dict[str, GreateyesSettingsModel]] = None
