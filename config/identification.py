from enum import StrEnum

from pydantic import BaseModel


class UserCapabilities(StrEnum):
    """MAST user capability enumeration for type safety and IDE autocomplete"""

    CAN_VIEW = "canView"
    CAN_CHANGE_CONFIGURATION = "canChangeConfiguration"
    CAN_USE_CONTROLS = "canUseControls"
    CAN_CHANGE_USERS = "canChangeUsers"
    CAN_OWN_TASKS = "canOwnTasks"
    # The 'planners' group in the config DB has carried this capability while the enum
    # did not, so every `GroupConfig(**group)` over that document raised ValidationError
    # -- taking down Config.get_users() and get_user() outright, and with them the
    # controller's /config/users and /config/user endpoints. The capability is real and
    # in use (MAST_gui's plans view and templates key on "canManagePlans"); only this
    # member was missing.
    CAN_MANAGE_PLANS = "canManagePlans"


class UserConfig(BaseModel):
    name: str
    full_name: str | None = None
    groups: list[str]
    #: Derived from group membership by `Config.get_users()`, never stored in a `users`
    #: document. It was previously required, which forced `get_users` to inject
    #: `user_dict["capabilities"] = []` into the cached document before validating --
    #: a write into the shared configuration store on every call. Defaulting it here is
    #: what lets that injection go away.
    capabilities: list[UserCapabilities] = []
    email: str | None = None
    password: str | None = None

    # `picture` (a raw JPEG, `bytes`) was removed on 2026-08-31 along with the pictures
    # themselves. It was `exclude=True`, so it never reached any serialized output, and
    # nothing in the fleet read it; avatars come from the social-login provider instead
    # (MAST_gui `accounts/adapter.py` sets `user.avatar_url` from the OAuth claim). No
    # `extra` is configured, so pydantic ignores the key if an old document still has it.


class GroupConfig(BaseModel):
    name: str
    members: list[str] | None = None
    capabilities: list[UserCapabilities] | None = None
