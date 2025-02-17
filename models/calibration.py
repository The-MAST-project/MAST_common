from typing import Optional
from pydantic import BaseModel, field_validator, ValidationError, model_validator
from common.config import Config


class CalibrationModel(BaseModel):
    lamp_on: Optional[bool] = False
    filter: Optional[str] = None

    @model_validator(mode='after')
    def validate_calibration(cls, model):
        if model.lamp_on:
            if not model.filter:
                raise ValidationError("a filter must be provided when lamp is 'on'")
            filters: dict = Config().get_specs()['wheels']['ThAr']['filters']
            if model.filter is not None and model.filter not in filters.values():
                raise ValidationError(f"filter '{model.filter}' not in {filters.values()}")
        return model
