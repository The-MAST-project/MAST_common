from pydantic import BaseModel, Field
from typing import Optional, Union, Literal

from common.models.deepspec import DeepspecModel
from common.models.highspec import HighspecModel
from common.models.calibration import CalibrationModel


class SpectrographModel(BaseModel):
    instrument: Literal['highspec', 'deepspec']
    exposure_duration: float
    number_of_exposures: Optional[int] = 1
    calibration: Optional[CalibrationModel]
    spec: Union[HighspecModel, DeepspecModel] = Field(discriminator='instrument')
