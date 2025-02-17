from typing import Optional
from pydantic import BaseModel, field_validator, ValidationError, model_validator
from common.config import Config


class CalibrationModel(BaseModel):
    lamp_on: Optional[bool] = False
    filter: Optional[str]

    @field_validator('filter')
    def validate_filter(cls, value):
        filters: dict = Config().get_specs()['wheels']['ThAr']['filters']
        if value is not None and value not in filters.values():
            raise ValidationError(f"filter '{value}' not in {filters.values()}")
        return value

    @model_validator(mode='after')
    def validate_calibration(self, value):
        if self.lamp_on and not self.filter:
            raise ValidationError("a filter must be provided when lamp is 'on'")
        return value