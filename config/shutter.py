from pydantic import BaseModel


class ShutterConfig(BaseModel):
    """Configuration for shutter settings."""

    open_time: int  # time it takes to open (ms)
    close_time: int  # time it takes to close (ms)
    automatic: bool = True  # Whether the shutter operates automatically
