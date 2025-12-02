from enum import StrEnum

from pydantic import BaseModel, Field


class UserCapabilities(StrEnum):
    """MAST user capability enumeration for type safety and IDE autocomplete"""
    CAN_VIEW = "canView"
    CAN_CHANGE_CONFIGURATION = "canChangeConfiguration"
    CAN_USE_CONTROLS = "canUseControls"
    CAN_CHANGE_USERS = "canChangeUsers"
    CAN_OWN_TASKS = "canOwnTasks"


class UserConfig(BaseModel):
    name: str
    full_name: str | None = None
    groups: list[str]
    capabilities: list[UserCapabilities]
    picture: bytes | None = Field(default=None, exclude=True)
    email: str | None = None
    password: str | None = None
    model_config = {"arbitrary_types_allowed": True}


class GroupConfig(BaseModel):
    name: str
    members: list[str] | None = None
    capabilities: list[UserCapabilities] | None = None
