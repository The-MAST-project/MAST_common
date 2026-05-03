from enum import IntFlag, auto
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

from common.utils import function_name

Disperser = Literal["Ca", "Mg", "Halpha", "Empty"]
SpecName = Literal["Deepspec", "Highspec"]


class SpecActivities(IntFlag):
    Checking = auto()
    Positioning = auto()
    Acquiring = auto()
    Exposing = auto()
    StartingUp = auto()
    ShuttingDown = auto()


class SpecId(IntFlag):
    Deepspec = auto()
    Highspec = auto()


class SpecExposureSettings(BaseModel):
    """
    Defines the settings for spectrograph camera exposures
    """

    exposure_duration: float
    number_of_exposures: int | None = 1
    x_binning: int | None = 1
    y_binning: int | None = 1
    image_full_name: str | None = None
    gain: int | None = None

    def model_post_init(self, __context: Any):

        if self.image_full_name is not None:
            Path(self.image_full_name).parent.mkdir(parents=True, exist_ok=True)
        else:
            raise ValueError(f"{function_name()}: image_full_name must be provided")

    @property
    def number_in_sequence(self) -> int | None:
        return self._number_in_sequence

    @number_in_sequence.setter
    def number_in_sequence(self, value):
        self._number_in_sequence = value


class SpecAcquisitionSettings:
    def __init__(
        self,
        spec_name: SpecName,
        lamp_on: bool,
        exposure_duration: float,
        filter_name: Optional[str] = None,
        number_of_exposures: Optional[int] = 1,
        grating: Optional[Disperser] = None,
        x_binning: Optional[int] = 1,
        y_binning: Optional[int] = 2,
        output_folder: Optional[str] = None,
    ):
        self.spec: SpecId = (
            SpecId.Deepspec if spec_name == "Deepspec" else SpecId.Highspec
        )
        self.grating = grating
        self.lamp_on = lamp_on
        self.filter_name = filter_name
        self.exposure_duration = exposure_duration
        self.number_of_exposures = number_of_exposures
        self.x_binning = x_binning
        self.y_binning = y_binning
        self.output_folder = output_folder  # A folder path underneath the Filer().root


FiberStageLiteral = Literal["Deepspec", "Highspec"]
StageLiteral = Disperser | FiberStageLiteral
BinningLiteral = Literal[1, 2, 4]
BinningLiteralStr = Literal["1", "2", "4"]

DeepspecBands = Literal["I", "G", "R", "U"]

FilterPositions = Literal["1", "2", "3", "4", "5", "6", "default"]
WheelNames = Literal["ThAr", "qTh"]
SpecStageNames = Literal["focusing", "disperser", "fiber"]
SpecInstruments = Literal["deepspec", "highspec"]
GratingNames = Literal["Ca", "Halpha", "Mg", "Future"]
