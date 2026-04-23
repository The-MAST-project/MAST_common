from pydantic import BaseModel

from .newton import NewtonCameraSettingsModel
from ..spec import Disperser


class HighspecSettings(BaseModel):
    disperser: Disperser
    camera: NewtonCameraSettingsModel | None = None
