from pydantic import BaseModel

from common.models.calibration import CalibrationSettings
from common.models.deepspec import DeepspecSettings
from common.models.highspec import HighspecSettings
from common.spec import SpecInstruments


class SpectrographModel(BaseModel):
    instrument: SpecInstruments
    exposure_duration: float  # required exposure duration
    max_exposure_duration: float | None = (
        None  #  Cannot be batched together with plans having longer exposure durations
    )
    number_of_exposures: int | None = 1
    calibration: CalibrationSettings | None
    settings: HighspecSettings | DeepspecSettings | None = None
