import datetime
import logging
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import numpy as np
import ulid
from pydantic import BaseModel, Field

import common.asi as asi
from common.dlipowerswitch import PowerStatus
from common.interfaces.components import Component, ComponentStatus
from common.mast_logging import init_log
from common.paths import PathMaker
from common.rois import SkyRoi, SpecRoi, UnitRoi
from common.utils import function_name

logger = logging.Logger(__name__)
init_log(logger)

class ImagerTypes(str, Enum):
    Ascom = "ascom"
    Phd2 = "phd2"
    Zwo = "zwo"


# class ImagerBinning(BaseModel):
#     x: int = 1
#     y: int = 1

#     def __str__(self):
#         return f"{self.x}x{self.y}"

class ImagerPixel(BaseModel):
    x: int
    y: int


class ImagerRoi(BaseModel):
    """
    MAST Region-Of-Interest.  Always conditioned to conform to:

    - MAST constraints:
        - The center pixel is expected to be on the optical axis of the system, at any binning

    - asi ZWO constraints:
        - At any supported binning the width must be congruent to mod 8 == 0 and the height must be congruent to mod 2 == 0

    Since both width and height are EVEN there is no center-pixel.  The center pixel will always be the highest pixel
    of the lower half of the dimension (width/height).

    An ImagerRoi can be derived from other ROIs:
    - UnitRoi or SkyRoi: both don't specify center pixel
    - SpecRoi: specifies a center pixel
    """

    x: int = 0  # start.x
    y: int = 0  # start.y
    width: int = asi.ASI_294MM_WIDTH
    height: int = asi.ASI_294MM_HEIGHT
    _center: ImagerPixel | None = None

    def model_post_init(self, context: dict[str, Any] | None):
        from common.asi import ASI_294MM_SUPPORTED_BINNINGS_SET

        if self._center:    # _center was specified, it will govern width and height
            pass
        else:               # no _center was specified, it will be governed by width/height
            self._center = ImagerPixel(x=self.x + (self.width // 2) - 1 , y=self.y + (self.height // 2) - 1)

        # adjust width/height according to _center and start point
        half_width = min(self._center.x - self.x, (self.x + self.width) - self._center.x)   # min(left-of-center, right-of-center)
        half_height = min(self._center.y - self.y, (self.y + self.height) - self._center.y) # min(below-center, above-center)

        self.width = half_width * 2
        self.height = half_height *2

        # apply asi constraints
        self.width -= self.width % (8 * max(ASI_294MM_SUPPORTED_BINNINGS_SET))
        self.height -= self.height % (2 * max(ASI_294MM_SUPPORTED_BINNINGS_SET))

        # adjust start point according to _center and possibly adjusted width and height
        self.x = self._center.x - (self.width // 2)
        self.y = self._center.y - (self.height // 2)

        logger.debug(f"{function_name()}: {self=}")

    def __str__(self):
        return f"{self.width}x{self.height}@{self.x},{self.y}"

    def __repr__(self):
        return f"ImagerRoi(x={self.x}, y={self.y}, width={self.width}, height={self.height}, _center={self._center})"

    @staticmethod
    def from_other(roi: SkyRoi | SpecRoi | UnitRoi):
        """
        Makes an ImagerRoi from other types of ROIs
        """

        msg = f"{function_name()}: {roi=} => "
        width: int = roi.width
        height: int = roi.height

        if isinstance(roi, SkyRoi):
            start = ImagerPixel(x=roi.sky_x - (roi.width // 2), y=roi.sky_y - (roi.height // 2))
            center = ImagerPixel(x=roi.sky_x, y=roi.sky_y)
        elif isinstance(roi, SpecRoi):
            start = ImagerPixel(x=roi.fiber_x - (roi.width // 2), y=roi.fiber_y - (roi.height // 2))
            center = ImagerPixel(x=roi.fiber_x, y=roi.fiber_y)
        elif isinstance(roi, UnitRoi):
            start = ImagerPixel(x=roi.center_x - (roi.width // 2), y=roi.center_y - (roi.height // 2))
            center = ImagerPixel(x=roi.center_x, y=roi.center_y)
        else:
            raise ValueError(f"ImagerRoi.from_other(): unknown type {type(roi)}")

        # adjust dimensions around the center pixel
        width = min(width, (center.x - start.x) * 2)
        width -= width % 8
        height = min(height, (center.y - start.y) * 2)
        height -= height % 2

        # adjust origin accordingly
        start.x = max(0, center.x - (width // 2))
        start.y = max(0, center.y - (height // 2))

        ret = ImagerRoi(
            x=start.x,
            y=start.y,
            width=width,
            height=height,
            _center=center
        )
        logger.debug(msg + f"{ret=}")
        return ret

    def binned(self, binning: int | None):

        b = binning or 1

        return ImagerRoi(x=self.x // b, y=self.y // b, width=self.width // b, height=self.height // b)

class ImagerSettings(BaseModel):
    """
    Multipurpose exposure context

    Callers to start_exposure() fill in:
    - seconds - duration in seconds
    - base_folder - [optional] supplied folder under which the new folder/file will reside
    - gain - to be applied to the self by start_exposure() (absolute value)
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
    binning: asi.ASI_294MM_SUPPORTED_BINNINGS_LITERAL
    gain: int | None = None
    roi: ImagerRoi | None = None
    tags: dict | None = {}
    save: bool = True
    fits_cards: dict[str, tuple] | None = {}
    start: datetime.datetime = Field(default=datetime.datetime.now(), exclude=True)
    file_name_parts: list[str] = Field(default=[], exclude=True)
    folder: str | None = Field(default=None, exclude=True)
    dont_bump_sequence: bool = False
    format: asi.ValidOutputFormats = "raw16"

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
                self.image_path = Path(self.image_path).as_posix()
                folder = Path(self.image_path).parent
                self.folder = str(folder.as_posix())
                folder.mkdir(parents=True, exist_ok=True)
            elif self.base_folder is not None:
                folder = Path(self.base_folder)
                folder.mkdir(parents=True, exist_ok=True)
                self.folder = str(folder)
                self.make_file_name(dont_bump_sequence=self.dont_bump_sequence)

    def model_dump(self, **kwargs):
        base = super().model_dump(**kwargs)
        base["image_path"] = Path(base["image_path"]).as_posix()
        return base

    def make_file_name(
        self, additional_tags: dict | None = None, dont_bump_sequence: bool = False
    ):
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
        if self.roi:
            self.file_name_parts.append(f"binned_roi={self.roi.binned(self.binning)}")

        self.image_path = str(
            Path(self.folder, ",".join(self.file_name_parts) + ".fits").as_posix()
        )
        pass

class ImagerPublicSettings(BaseModel):
    seconds: float
    binning: int
    gain: int
    roi: ImagerRoi


class ImagerSequenceOfExposures(BaseModel):
    exposure_settings: ImagerPublicSettings
    repeats: int = 1
    pause_between_exposures: float | None = None
    disconnect_camera: bool = False
    tell_guider_to_start: None | Literal["loop", "guide", "nothing"] = "guide"
    delay_before_telling_guider: float | None = None

class ImagerExposure(BaseModel):
    file: str | None = None
    seconds: float | None = None
    date: str | None = None
    start: datetime.datetime = Field(
        default_factory=datetime.datetime.now, exclude=True
    )


class ImagerStatus(PowerStatus, ComponentStatus):
    identifier: str | None = None
    camera_x_size: int | None = None
    camera_y_size: int | None = None
    errors: list[str] | None = None
    set_point: float | None = None
    temperature: float | None = None
    cooler_on: bool | None = None
    cooler_power: float | None = None
    latest_exposure: ImagerExposure | None = None
    latest_settings: ImagerSettings | None = None
    date: str | None = None
    backend: object | None = None


class ImagerExposureSeries:
    """
    Represents a series of exposures taken by the imager.
    This is used to maintain context for a series of exposures, e.g. in PHD2.
    """

    def __init__(self, purpose: str | None = None):
        self.series_id: str = str(ulid.ULID())
        self.purpose: str | None = purpose


class ImagerInterface(Component, ABC):
    current_exposure_series: ImagerExposureSeries | None = None
    ccd_temp_at_mid_exposure: float | None = None
    parent_imager = None

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
        Maintains an exposure series context by using a unique series id.
        This method should be called before starting a series of exposures.
        It returns a unique series id that should be used in subsequent calls to end_exposure_series().

        Some imagers (e.g. PHD2) may need to stop guiding before an exposure series, so this method
        should be called before starting the series.
        """
        if self.current_exposure_series is not None:
            raise ValueError(
                "ImagerInterface.start_exposure_series(): already in an exposure series "
                + f"id={self.current_exposure_series.series_id}, purpose={self.current_exposure_series.purpose}"
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
                f"ImagerInterface.end_exposure_series(): series_id mismatch {series.series_id=} != "
                + f"{self.current_exposure_series=}"
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
