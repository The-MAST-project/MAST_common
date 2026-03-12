from pydantic import BaseModel, ValidationError, model_validator

from common.config import Config

EmptyFilter = "Empty"


class CalibrationSettings(BaseModel):
    lamp_on: bool | None = False
    filter: str | None = None

    @model_validator(mode="after")
    @classmethod
    def validate_calibration(cls, model):
        if model.lamp_on is not None and model.lamp_on:
            if not model.filter:
                model.filter = EmptyFilter
            filters: dict = Config().get_specs().wheels["ThAr"].filters
            if model.filter is not None and model.filter not in filters.values():
                raise ValidationError(
                    f"filter '{model.filter}' not in {filters.values()}"
                )
        return model
