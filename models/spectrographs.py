from pydantic import BaseModel, Field

from common.models.calibration import CalibrationSettings
from common.models.deepspec import DeepspecSettings
from common.models.highspec import HighspecSettings
from common.spec import SpecInstruments


class SpectrographModel(BaseModel):
    instrument: SpecInstruments | None = Field(
        default=None,
        json_schema_extra={"ui": {
            "label": "Instrument",
            "widget": "select",
            "options": ["highspec", "deepspec"],
            "required": True,
            "summary": True,
        }},
    )
    exposure_duration: float | None = Field(
        default=None,
        json_schema_extra={"ui": {
            "hidden": True,
        }},
    )
    number_of_exposures: int | None = Field(
        default=None,
        json_schema_extra={"ui": {
            "hidden": True,
        }},
    )
    calibration: CalibrationSettings | None = Field(
        default=None,
        json_schema_extra={"ui": {
            "label": "Calibration",
        }},
    )
    settings: HighspecSettings | DeepspecSettings | None = Field(
        default=None,
        json_schema_extra={"ui": {
            "hidden": True,
        }},
    )
