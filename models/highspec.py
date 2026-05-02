from pydantic import BaseModel

from common.models.newton import NewtonSettingsConfig
from common.spec import Disperser


class HighspecSettings(BaseModel):
    disperser: Disperser
    camera: NewtonSettingsConfig | None = None
