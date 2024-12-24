from typing import Optional, Literal
from enum import IntFlag, auto

SpecGrating = Literal['Ca', 'Mg', 'Halpha', 'Empty']
SpecName = Literal['Deepspec', 'Highspec']

class SpecActivities(IntFlag):
    Checking = auto()
    Positioning = auto()
    Acquiring = auto()
    Exposing = auto()


class SpecId(IntFlag):
    Deepspec = auto()
    Highspec = auto()

class SpecCameraExposureSettings:
    """
    Defines the settings for spectrograph camera exposures
    """
    def __init__(self,
                 exposure_duration: float,
                 number_of_exposures: Optional[int] = 1,
                 x_binning: Optional[int] = 1,
                 y_binning: Optional[int] = 2,
                 output_folder: Optional[str] = None,
                 ):
        self.exposure_duration = exposure_duration
        self.number_of_exposures = number_of_exposures
        self.x_binning = x_binning
        self.y_binning = y_binning
        self.output_folder = output_folder  # A folder path underneath the Filer().root
        self._number_in_sequence: int | None = None

    @property
    def number_in_sequence(self) -> int:
        return self._number_in_sequence

    @number_in_sequence.setter
    def number_in_sequence(self, value):
        self._number_in_sequence = value


class SpecAcquisitionSettings:
    def __init__(self,
                 spec_name: SpecName,
                 lamp_on: bool,
                 exposure_duration: float,
                 filter_name: Optional[str] = None,
                 number_of_exposures: Optional[int] = 1,
                 grating: Optional[SpecGrating] = None,
                 x_binning: Optional[int] = 1,
                 y_binning: Optional[int] = 2,
                 output_folder: Optional[str] = None,
                 ):
        self.spec: SpecId = SpecId.Deepspec if spec_name == 'Deepspec' else SpecId.Highspec
        self.grating = grating
        self.lamp_on = lamp_on
        self.filter_name = filter_name
        self.exposure_duration = exposure_duration
        self.number_of_exposures = number_of_exposures
        self.x_binning = x_binning
        self.y_binning = y_binning
        self.output_folder = output_folder  # A folder path underneath the Filer().root


GratingsStageLiteral = Literal['Ca', 'Mg', 'Halpha', 'Future']
CameraStageLiteral = Literal['Deepspec', 'Highspec']
StageLiteral = GratingsStageLiteral | CameraStageLiteral

