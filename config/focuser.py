from pydantic import BaseModel, Field

from common.config.identification import UserCapabilities


class FocuserConfig(BaseModel):
    """Configuration for the telescope focuser."""

    ascom_driver: str = Field(
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        }
    )
    known_as_good_position: int = Field(
        ge=0,
        lt=50000,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "number",
                "unit": "ticks",
                "label": "Known As Good Position",
                "tooltip": "Latest successful autofocus position",
                "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION],
            },
        },
    )
