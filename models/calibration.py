from pydantic import BaseModel, Field, ValidationError, model_validator

from common.config import Config

EmptyFilter = "Empty"


class CalibrationSettings(BaseModel):
    lamp_on: bool | None = Field(
        default=False,
        json_schema_extra={"ui": {
            "label": "ThAr lamp",
            "widget": "checkbox",
        }},
    )
    filter: str | None = Field(
        default=None,
        json_schema_extra={"ui": {
            "label": "ThAr filter",
            "widget": "select",
            "options": [],
            "options_key": "filter_options",
            "tooltip": "ThAr filter; required when ThAr lamp is on",
        }},
    )

    @model_validator(mode="after")
    @classmethod
    def validate_calibration(cls, model):
        if model.lamp_on is not None and model.lamp_on:
            if not model.filter:
                model.filter = EmptyFilter
            filters = Config().get_thar_filters()
            if model.filter is not None and model.filter not in filters:
                raise ValidationError(
                    f"filter '{model.filter}' not in {filters}"
                )
        return model
