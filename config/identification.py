from pydantic import BaseModel, Field


class UserConfig(BaseModel):
    name: str
    full_name: str | None = None
    groups: list[str]
    capabilities: list[str]
    picture: bytes | None = Field(default=None, exclude=True)
    email: str | None = None
    password: str | None = None
    model_config = {"arbitrary_types_allowed": True}


class GroupConfig(BaseModel):
    name: str
    members: list[str] | None = None
    capabilities: list[str] | None = None
