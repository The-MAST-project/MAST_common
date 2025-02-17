from pydantic import BaseModel, Field, field_validator, ValidationError, model_validator
from typing import Optional, Literal, Union
from common.models.deepspec import DeepspecModel
from common.models.highspec import HighspecModel
from common.models.calibration import CalibrationModel


class SpectrographModel(BaseModel):
    exposure: float
    number_of_exposures: Optional[int] = 1
    calibration: Optional[CalibrationModel]
    spec: Union[HighspecModel, DeepspecModel]

    class Config:
        # Set the discriminating field
        smart_union = True
        discriminator = 'instrument'
