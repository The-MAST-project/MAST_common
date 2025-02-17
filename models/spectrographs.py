from pydantic import BaseModel, Field, field_validator, ValidationError, model_validator
from typing import Optional, Literal
from common.config import Config
from common.models.deepspec import DeepspecModel
from common.models.highspec import HighspecModel


class SpectrographModel(BaseModel):
    instrument: Literal['highspec', 'deepspec']
    # calibration: Optional[CalibrationModel]
    spec: HighspecModel | DeepspecModel = Field(discriminator='instrument')

    @model_validator(mode='after')
    def validate(self, values):
        return self
