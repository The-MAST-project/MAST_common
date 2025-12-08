from pydantic import BaseModel, Field

from common.config.identification import UserCapabilities


class FocuserConfig(BaseModel):
    """Configuration for the telescope focuser."""

    ascom_driver: str = Field(
        json_schema_extra={
            "hidden": True,
        }
    )
    known_as_good_position: int = Field(
        ge=0,
        lt=50000,
        json_schema_extra={
            "editable": True,
            "ui_widget": "number",
            "ui_unit": "steps",
            "tooltip": "A position known to be good for imaging",
            "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION],
        },
    )
