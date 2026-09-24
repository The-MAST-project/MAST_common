"""The ``opmode`` configuration field, shared by ``UnitConfig`` and ``SpecsConfig``."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from common.config.identification import UserCapabilities
from common.opmode import DEFAULT_OPMODE, OpMode


def opmode_field() -> Any:
    """A defaulted ``opmode`` field: the ``units.common`` merge requires every field to have one."""
    return Field(
        default=DEFAULT_OPMODE,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "select",
                "options": [mode.value for mode in OpMode],
                "label": "Operating mode",
                "tooltip": "automatic: the app starts up when run; controlled: it stands by for the control machine",
            },
            "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION.value],
        },
    )
