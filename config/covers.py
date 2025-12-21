from pydantic import BaseModel


class CoversConfig(BaseModel):
    """Configuration for the telescope covers."""

    ascom_driver: str
