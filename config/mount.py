from pydantic import BaseModel


class MountConfig(BaseModel):
    """Configuration for the telescope mount."""

    ascom_driver: str
