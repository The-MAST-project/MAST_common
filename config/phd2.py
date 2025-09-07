from pydantic import BaseModel


class PHD2SettleConfig(BaseModel):
    """Configuration for PHD2 settle settings."""

    pixels: int
    time: int
    timeout: int


class PHD2Config(BaseModel):
    profile: str
    settle: PHD2SettleConfig
    validation_interval: float
