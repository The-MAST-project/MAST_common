from pydantic import BaseModel

from .power import OutletConfig


class ChillerConfig(BaseModel):
    power: OutletConfig
