from pydantic import BaseModel, Field, field_validator, ValidationError, model_validator
from typing import Optional, Literal
from common.config import Config
from common.models.deepspec import DeepspecModel
from common.models.highspec import HighspecModel

class CalibrationModel(BaseModel):
    lamp: Optional[bool] = False
    filter: Optional[str]

    @field_validator('filter')
    def validate_filter(cls, value):
        filters: dict = Config().get_specs()['wheels']['ThAr']['filters']
        if value is not None and value not in filters.values():
            raise ValidationError(f"filter '{value}' not in {filters.values()}")
        return value

    @model_validator(mode='after')
    def validate_calibration(self, value):
        if self.lamp and not self.filter:
            raise ValidationError("a filter must be provided when lamp is 'on'")
        return value


class SpectrographModel(BaseModel):
    instrument: str
    spec: HighspecModel | DeepspecModel = Field(discriminator='instrument')

    @model_validator(mode='after')
    def validate(self, values):
        return self
