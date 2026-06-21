from pydantic import BaseModel, Field, model_validator

from common.models.calibration import CalibrationSettings
from common.models.deepspec import DeepspecSettings
from common.models.highspec import HighspecSettings
from common.spec import SpecInstruments


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

    @model_validator(mode="after")
    def check_settings_type(self) -> "SpectrographModel":
        match self.instrument:
            case "highspec":
                if self.settings is not None and not isinstance(
                    self.settings, HighspecSettings
                ):
                    raise ValueError(
                        f"instrument='highspec' requires HighspecSettings, got {type(self.settings).__name__}"
                    )
            case "deepspec":
                if self.settings is not None and not isinstance(
                    self.settings, DeepspecSettings
                ):
                    raise ValueError(
                        f"instrument='deepspec' requires DeepspecSettings, got {type(self.settings).__name__}"
                    )
        return self
