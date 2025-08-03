import datetime
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import ulid
from pydantic import BaseModel, Field

from common.dlipowerswitch import PowerStatus
from common.interfaces.components import Component, ComponentStatus
from common.paths import PathMaker


class ImagerTypes(str, Enum):
    Ascom = "ascom"
    Phd2 = "phd2"
    Zwo = "zwo"


class ImagerBinning(BaseModel):
    x: int = 1
    y: int = 1

    def __str__(self):
        return f"{self.x}x{self.y}"


class ImagerRoi(BaseModel):
    """
    Lower left corner of the ROI, and its width and height.
    """

    x: int = 0
    y: int = 0
    width: int = 1000
    height: int = 1000

    def __str__(self):
        return f"{self.x},{self.y},{self.width},{self.height}"

    @staticmethod
    def from_other(binning: ImagerBinning, other):
        """
        An imager ROI has a starting pixel (x, y) at lower left corner, width and height
        """
        if not binning:
            binning = ImagerBinning(x=1, y=1)

        if other.width is None or other.height is None:
            raise ValueError(
                f"ImagerRoi.from_other(): width or height is None in {other}"
            )

        if hasattr(other, "sky_x") and hasattr(other, "sky_y"):
            center_x = other.sky_x
            center_y = other.sky_y
        elif hasattr(other, "fiber_x") and hasattr(other, "fiber_y"):
            center_x = other.fiber_x
            center_y = other.fiber_y
        elif hasattr(other, "center_x") and hasattr(other, "center_y"):
            center_x = other.center_x
            center_y = other.center_y
        else:
            raise ValueError(f"ImagerRoi.from_other(): unknown type {type(other)}")

        return ImagerRoi(
            x=(center_x - int(other.width / 2)) * binning.x,
            y=(center_y - int(other.height / 2)) * binning.y,
            width=other.width * binning.x,
            height=other.height * binning.y,
        )


class ImagerSettings(BaseModel):
    """
    Multipurpose exposure context

    Callers to start_exposure() fill in:
    - seconds - duration in seconds
    - base_folder - [optional] supplied folder under which the new folder/file will reside
    - gain - to be applied to the self by start_exposure()
    - binning - ditto
    - roi - ditto
    - tags - a flat dictionary of tags, will be added to the file name as ',name=value' or
       just ',name' if the value is None
    - save - whether to save to file or just keep in memory
    - fits_cards - to be added to the default ones
    """

    seconds: float
    base_folder: str | None = None
    image_path: str | None = None
    binning: ImagerBinning | None = ImagerBinning(x=1, y=1)
    gain: int | None = None
    roi: ImagerRoi | None = None
    tags: dict | None = {}
    save: bool = True
    fits_cards: dict[str, tuple] | None = {}
    start: datetime.datetime = Field(default=datetime.datetime.now(), exclude=True)
    file_name_parts: list[str] = Field(default=[], exclude=True)
    folder: str | None = Field(default=None, exclude=True)
    dont_bump_sequence: bool = False

    def model_post_init(self, context: dict[str, Any] | None):  # noqa: C901
        defaults: ImagerSettings | None = None
        if context and (imager := context.get("imager")):
            defaults = imager.default_settings

        if defaults:
            if not self.seconds and defaults.seconds:
                self.seconds = defaults.seconds
            if not self.gain and defaults.gain:
                self.gain = defaults.gain
            if not self.binning and defaults.binning:
                self.binning = defaults.binning
            if not self.base_folder and defaults.base_folder:
                self.base_folder = defaults.base_folder
            if not self.roi and defaults.roi:
                self.roi = defaults.roi

        if self.save:
            if self.image_path is None and self.base_folder is None:
                raise ValueError(
                    "ImagerSettings: either 'image_path' or 'base_folder' MUST be supplied when save=True"
                )

            if self.image_path is not None:
                folder = Path(self.image_path).parent
                self.folder = str(folder.as_posix())
                folder.mkdir(parents=True, exist_ok=True)
            elif self.base_folder is not None:
                folder = Path(self.base_folder)
                folder.mkdir(parents=True, exist_ok=True)
                self.folder = str(folder)
                self.make_file_name(dont_bump_sequence=self.dont_bump_sequence)

    def make_file_name(self, additional_tags: dict | None = None, dont_bump_sequence: bool = False):
        """
        Makes the file part of the image path.  This will:
        - generate current seq= and time= file name parts
        - prepend optional additional_tags to those passed to the constructor

        :param additional_tags: tags specific to THIS making of the file name
        :return:
        """
        if not self.folder:
            raise ValueError(
                "ImagerSettings: 'folder' must be set before making file name"
            )

        self.file_name_parts = []
        self.file_name_parts.append(
            f"seq={PathMaker().make_seq(self.folder, start_with=-1, dont_bump=dont_bump_sequence)}"
        )
        self.file_name_parts.append(f"time={PathMaker().current_utc()}")

        tags = {}
        if additional_tags:
            tags = additional_tags
        if self.tags:
            tags.update(self.tags)
        for k, v in tags.items():
            self.file_name_parts.append(f"{k}" if v is None else f"{k}={v}")

        self.file_name_parts.append(f"seconds={self.seconds}")
        self.file_name_parts.append(f"binning={self.binning}")
        self.file_name_parts.append(f"gain={self.gain}")
        self.file_name_parts.append(f"roi={self.roi}")

        self.image_path = str(
            Path(self.folder, ",".join(self.file_name_parts) + ".fits")
        )
        pass


class ImagerExposure(BaseModel):
    file: str | None = None
    seconds: float | None = None
    date: str | None = None
    start: datetime.datetime = Field(
        default_factory=datetime.datetime.now, exclude=True
    )


class ImagerStatus(PowerStatus, ComponentStatus):
    type: ImagerTypes | None = None
    model: str | None = None
    camera_x_size: int | None = None
    camera_y_size: int | None = None
    errors: list[str] | None = None
    set_point: float | None = None
    temperature: float | None = None
    cooler: bool = False
    cooler_power: float | None = None
    latest_exposure: ImagerExposure | None = None
    latest_settings: ImagerSettings | None = None
    date: str | None = None


class ImagerExposureSeries:
    """
    Represents a series of exposures taken by the imager.
    This is used to maintain context for a series of exposures, e.g. in PHD2.
    """

    def __init__(self, purpose: str | None = None):
        self.series_id: str = str(ulid.ulid())
        self.purpose: str | None = purpose


class ImagerInterface(Component, ABC):

    current_exposure_series: ImagerExposureSeries | None = None

    @property
    @abstractmethod
    def connected(self) -> bool:
        """
        Check if the imager is connected.
        :return: True if connected, False otherwise
        """
        pass

    @connected.setter
    @abstractmethod
    def connected(self, value: bool):
        """
        Connect to the imager.
        This method should be called before any other methods that require a connection.
        """
        pass

    @property
    @abstractmethod
    def camera_x_size(self) -> int | None:
        """
        Get the camera's X size in pixels.
        """
        pass

    @property
    @abstractmethod
    def camera_y_size(self) -> int | None:
        """
        Get the camera's Y size in pixels.
        """
        pass

    @abstractmethod
    def start_exposure(self, settings: ImagerSettings):
        if self.current_exposure_series is None:
            raise ValueError(
                "ImagerInterface.start_exposure(): must call start_exposure_series() before starting an exposure"
            )
        self.latest_settings = settings
        pass

    @abstractmethod
    def start_exposure_series(self, purpose: str | None = None) -> ImagerExposureSeries:
        """
        Maintains and exposure series context by using a unique series id.
        This method should be called before starting a series of exposures.
        It returns a unique series id that should be used in subsequent calls to end_exposure_series().

        Some imagers (e.g. PHD2) may need to stop guiding before an exposure series, so this method
        should be called before starting the series.
        """
        if self.current_exposure_series is not None:
            raise ValueError(
                f"ImagerInterface.start_exposure_series(): already in an exposure series id={self.current_exposure_series.series_id}, purpose={self.current_exposure_series.purpose}"
            )
        self.current_exposure_series = ImagerExposureSeries(purpose=purpose)
        return self.current_exposure_series

    @abstractmethod
    def end_exposure_series(self, series: ImagerExposureSeries):
        """
        Do any post-exposure series cleanup required by the imager (e.g. PHD2 should resume guiding if it was stopped).
        This method is called after the exposure series is completed.
        """
        if series is None:
            raise ValueError(
                "ImagerInterface.end_exposure_series(): series cannot be None"
            )
        if self.current_exposure_series is None:
            raise ValueError(
                "ImagerInterface.end_exposure_series(): no self.current_exposure_series to end"
            )
        if series.series_id != self.current_exposure_series.series_id:
            raise ValueError(
                f"ImagerInterface.end_exposure_series(): series_id mismatch {series.series_id=} != {self.current_exposure_series=}"
            )
        self.current_exposure_series = None

    @abstractmethod
    def stop_exposure(self):
        pass

    @property
    @abstractmethod
    def can_image_to_memory(self) -> bool:
        """
        Check if the imager can capture images to memory.
        :return: True if the imager can capture images to memory, False otherwise
        """
        pass

    @abstractmethod
    def abort_exposure(self):
        pass

    @abstractmethod
    def can_send_image_ready_event(self) -> bool:
        pass

    @abstractmethod
    def wait_for_image_ready(self):
        pass

    @abstractmethod
    def can_send_image_saved_event(self) -> bool:
        pass

    @abstractmethod
    def wait_for_image_saved(self):
        pass

    @property
    @abstractmethod
    def temperature(self) -> float:
        pass

    @property
    @abstractmethod
    def cooler_on(self) -> bool:
        """
        Check if the camera cooler is currently on.
        :return: True if the cooler is on, False otherwise
        """
        pass

    @cooler_on.setter
    @abstractmethod
    def cooler_on(self, onoff: bool):
        pass

    @property
    @abstractmethod
    def cooler_power(self) -> float | None:
        pass

    @property
    @abstractmethod
    def image_array(self) -> np.ndarray | None:
        """
        Get the image data from the imager.
        This method should be called after an exposure has been taken.
        """
        pass

    @property
    def full_frame(self) -> ImagerRoi:
        """
        Get the full frame ROI of the imager.
        """
        if self.camera_x_size is None or self.camera_y_size is None:
            raise ValueError(
                "Camera X and Y sizes must be set before getting full frame ROI"
            )

        return ImagerRoi(x=0, y=0, width=self.camera_x_size, height=self.camera_y_size)
