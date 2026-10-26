from pydantic import BaseModel, Field, model_validator

from common.models.calibration import CalibrationSettings
from common.models.constraints import RepeatsModel
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

    # The exposure series: what the spectrograph does with the light. Moved here from `Target`,
    # which describes the sky only (plans/control_acquisition_design.md 3.2).
    requested_exposure_duration: float | None = Field(
        default=None,
        gt=0,
        le=3600,
        json_schema_extra={
            "ui": {
                "error_message": "Positive decimal between 0 and 3600",
                "label": "Duration per exposure",
                "widget": "number",
                "unit": "seconds",
                "summary": True,
                "section": {
                    "label": "Exposure Series",
                    "tooltip": "Series of exposures to be scheduled for this target",
                },
            },
        },
    )
    max_exposure_duration: float | None = Field(
        default=None,
        gt=0,
        le=3600,
        json_schema_extra={
            "ui": {
                "error_message": "Positive decimal between 0 and 3600",
                "label": "Max duration per exposure",
                "widget": "number",
                "unit": "seconds",
                "tooltip": "Plans with longer durations will not be batched together,<br>&nbsp;to prevent over exposure",
                "section": "Exposure Series",
            }
        },
    )
    requested_number_of_exposures: int | None = Field(
        default=1,
        gt=0,
        json_schema_extra={
            "ui": {
                "error_message": "Positive integer",
                "label": "Number of exposures",
                "widget": "number",
                "required": True,
                "section": {
                    "label": "Exposure Series",
                },
            }
        },
    )
    repeats: RepeatsModel = Field(
        default_factory=RepeatsModel,
        json_schema_extra={
            "ui": {
                "label": "repeats",
                "tooltip": "When and how much should the exposure series be rescheduled?",
            }
        },
    )

    @model_validator(mode="after")
    def an_instrument_needs_a_duration(self) -> "SpectrographModel":
        if self.instrument is not None and self.requested_exposure_duration is None:
            raise ValueError(f"instrument={self.instrument!r} needs a requested_exposure_duration")
        return self

    @model_validator(mode="after")
    def check_settings_type(self) -> "SpectrographModel":
        match self.instrument:
            case "highspec":
                if self.settings is not None and not isinstance(self.settings, HighspecSettings):
                    raise ValueError(f"instrument='highspec' requires HighspecSettings, got {type(self.settings).__name__}")
            case "deepspec":
                if self.settings is not None and not isinstance(self.settings, DeepspecSettings):
                    raise ValueError(f"instrument='deepspec' requires DeepspecSettings, got {type(self.settings).__name__}")
        return self
