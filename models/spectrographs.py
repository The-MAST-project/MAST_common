from pydantic import BaseModel, Field

from .calibration import CalibrationSettings
from .deepspec import DeepspecSettings
from .highspec import HighspecSettings
from ..spec import SpecInstruments


class SpectrographModel(BaseModel):
    instrument: SpecInstruments | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Instrument",
                "widget": "select",
                "options": ["highspec", "deepspec"],
                "required": True,
                "summary": True,
            },
            "searchable": "exact",
        },
    )
    calibration: CalibrationSettings | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Calibration",
            }
        },
    )
    settings: HighspecSettings | DeepspecSettings | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )
