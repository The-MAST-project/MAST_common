from pydantic import BaseModel

from common.models.newton import NewtonCameraSettingsModel
from common.spec import Disperser


class HighspecSettings(BaseModel):
    disperser: Disperser
    camera: NewtonCameraSettingsModel | None = None
