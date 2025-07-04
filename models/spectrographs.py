from typing import Literal

from pydantic import BaseModel, Field

from common.models.calibration import CalibrationModel
from common.models.deepspec import DeepspecModel
from common.models.highspec import HighspecModel


class SpectrographModel(BaseModel):
    instrument: Literal["highspec", "deepspec"]
    exposure_duration: float
    number_of_exposures: int | None = 1
    calibration: CalibrationModel | None
    spec: HighspecModel | DeepspecModel = Field(discriminator="instrument")
