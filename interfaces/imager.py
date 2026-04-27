import logging
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Literal

import numpy as np
import ulid
from pydantic import BaseModel, Field

from common.activities import ImagerActivities
from common.interfaces.components import Component
from common.mast_logging import init_log
from common.models.statuses import ImagerSettings, ImagerRoi

logger = logging.Logger(__name__)
init_log(logger)


class ImagerTypes(StrEnum):
    Ascom = "ascom"
    Phd2 = "phd2"
    Zwo = "zwo"


# class ImagerBinning(BaseModel):
#     x: int = 1
#     y: int = 1

#     def __str__(self):
#         return f"{self.x}x{self.y}"


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


class ImagerExposureSeries(BaseModel):
    """
    Represents a series of exposures taken by the imager.
    This is used to maintain context for a series of exposures, e.g. in PHD2.
    """

    series_id: str = Field(default_factory=lambda: str(ulid.ULID()))
    purpose: str | None = None


class ImagerInterface(Component, ABC):
    current_exposure_series: ImagerExposureSeries | None = None
    ccd_temp_at_mid_exposure: float | None = None

    def __init__(self):
        Component.__init__(self, ImagerActivities)
        self.parent_imager = None

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
