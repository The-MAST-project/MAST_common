from pydantic import BaseModel

from common.models.greateyes import GreateyesSettingsModel


class DeepspecSettings(BaseModel):
    camera: dict[str, GreateyesSettingsModel] | None = None
