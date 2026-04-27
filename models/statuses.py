import datetime
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

import common.asi as asi
from common.activities import ActivitiesVerbal
from common.mast_logging import init_log
from common.rois import SkyRoi, SpecRoi, UnitRoi
from common.spec import FilterPositions, GratingNames, SpecInstruments, SpecStageNames, WheelNames
from common.utils import PathMaker, function_name

logger = logging.Logger(__name__)
init_log(logger)

StatusType = Literal["basic", "full"]


class BaseStatus(BaseModel):
    """Base class for unit status."""

    type: StatusType = "basic"
    detected: bool | None = None
    operational: bool | None = None
    why_not_operational: list[str] | None = None


class ComponentStatus(BaseStatus):
    connected: bool = False
    activities: int = 0
    activities_verbal: ActivitiesVerbal = None
    was_shut_down: bool = False
    model_config = {"arbitrary_types_allowed": True}


class PowerStatus(BaseModel):
    powered: bool = False


TriStateBool = bool | None


class OutletStatus(BaseModel):
    name: str | None = None
    state: TriStateBool = None

    def __repr__(self):
        return f"OutletStatus(name='{self.name}', state={self.state})"


class PowerSwitchStatus(BaseModel):
    host: str | None = None
    ipaddr: str | None = None
    detected: bool = False
    operational: bool = False
    why_not_operational: list[str] = []
    outlets: list[OutletStatus] = []

    def __repr__(self):
        return (
            f"PowerSwitchStatus(host='{self.host}', ipaddr='{self.ipaddr}', detected={self.detected}, operational={self.operational}, "
            + f"why_not_operational={self.why_not_operational})"
        )


# ASCOM stuff
class AscomDriverInfoModel(BaseModel):
    name: str
    description: str
    version: str
    connected: bool = False


class AscomStatus(BaseModel):
    ascom: AscomDriverInfoModel


# Covers stuff
# https://ascom-standards.org/Help/Developer/html/T_ASCOM_DeviceInterface_CoverStatus.htm
class CoversState(Enum):
    NotPresent = 0
    Closed = 1
    Moving = 2
    Open = 3
    Unknown = 4
    Error = 5


class CoverStatus(PowerStatus, AscomStatus, ComponentStatus):
    target_verbal: str | None = None
    state: CoversState | None = None
    state_verbal: str | None = None
    date: str | None = None


# Focuser stuff
class FocuserStatus(PowerStatus, AscomStatus, ComponentStatus):
    lower_limit: int | None = None
    upper_limit: int | None = None
    known_as_good_position: int | None = None
    position: int | None = None
    target: int | None = None
    target_verbal: str | None = None
    moving: bool = False
    date: str | None = None


# Guider stuff
class SkyQualityStatus(BaseModel):
    score: float | None = None
    state: str | None = None
    latest_update: str | None = None


class PHD2GuiderStatus(BaseModel):
    identifier: str | None = None
    is_guiding: bool = False
    is_settling: bool = False
    app_state: str | None = None
    avg_dist: float | None = None
    sky_quality: SkyQualityStatus | None = None


class ActivitiesStatus(BaseModel):
    activities: int | None = None
    activities_verbal: ActivitiesVerbal


class GuiderStatus(ActivitiesStatus):
    backend: PHD2GuiderStatus | None = None


# Stage stuff
class StageStatus(PowerStatus, ComponentStatus):
    info: dict | None = None
    presets: dict | None = None
    position: int | None = None
    at_preset: str | None = None
    target: int | None = None
    target_verbal: str | None = None
    date: str | None = None


# Mount stuff
class SpiralSettings(BaseModel):
    x: float
    y: float
    x_step_arcsec: float
    y_step_arcsec: float


class MountStatus(PowerStatus, AscomStatus, ComponentStatus):
    errors: list[str] | None = None
    target_verbal: str | None = None
    tracking: bool = False
    slewing: bool = False
    axis0_enabled: bool = False
    axis1_enabled: bool = False
    ra_j2000_hours: float | None = None
    dec_j2000_degs: float | None = None
    ha_hours: float | None = None
    lmst_hours: float | None = None
    fans: bool = False
    spiral: SpiralSettings | None = None
    date: str | None = None


# PHD2 Imager status
class PHD2ImagerStatus(ActivitiesStatus):
    identifier: str | None = None
    name: str = "phd2"
    operational: bool = False
    why_not_operational: list[str] = []
    connected: bool = False


# class NotPoweredStatus(BaseStatus):
#     def model_post_init(self, __context: Any) -> BaseStatus:
#         return BaseStatus(
#             powered=False,
#             detected=False,
#             operational=False,
#             why_not_operational=["Not powered"],
#         )


# class NotDetectedStatus(BaseStatus):
#     def model_post_init(self, __context: Any) -> BaseStatus:
#         return BaseStatus(
#             powered=True,
#             detected=False,
#             operational=False,
#             why_not_operational=["Not detected"],
#         )


class NotOperationalStatus(BaseStatus):
    def model_post_init(self, __context: Any) -> BaseStatus:
        reasons = __context.get("reasons", ["Not operational"])

        return BaseStatus(
            detected=True,
            operational=False,
            why_not_operational=reasons,
        )


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

        if self._center:  # _center was specified, it will govern width and height
            pass
        else:  # no _center was specified, it will be governed by width/height
            self._center = ImagerPixel(
                x=self.x + (self.width // 2) - 1, y=self.y + (self.height // 2) - 1
            )

        # adjust width/height according to _center and start point
        half_width = min(
            self._center.x - self.x, (self.x + self.width) - self._center.x
        )  # min(left-of-center, right-of-center)
        half_height = min(
            self._center.y - self.y, (self.y + self.height) - self._center.y
        )  # min(below-center, above-center)

        self.width = half_width * 2
        self.height = half_height * 2

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
            start = ImagerPixel(
                x=roi.sky_x - (roi.width // 2), y=roi.sky_y - (roi.height // 2)
            )
            center = ImagerPixel(x=roi.sky_x, y=roi.sky_y)
        elif isinstance(roi, SpecRoi):
            start = ImagerPixel(
                x=roi.fiber_x - (roi.width // 2), y=roi.fiber_y - (roi.height // 2)
            )
            center = ImagerPixel(x=roi.fiber_x, y=roi.fiber_y)
        elif isinstance(roi, UnitRoi):
            start = ImagerPixel(
                x=roi.center_x - (roi.width // 2), y=roi.center_y - (roi.height // 2)
            )
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
            x=start.x, y=start.y, width=width, height=height, _center=center
        )
        logger.debug(msg + f"{ret=}")
        return ret

    def binned(self, binning: int | None):
        b = binning or 1

        return ImagerRoi(
            x=self.x // b, y=self.y // b, width=self.width // b, height=self.height // b
        )


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
    use_set_limit_frame: bool = True

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


class FullUnitStatus(ComponentStatus, PowerStatus):
    """Full unit status with all components, returned from the unit itself."""

    type: StatusType = "full"
    id: int
    guiding: bool = False
    autofocusing: bool = False
    power_switch: PowerSwitchStatus | None = None
    mount: MountStatus | None = None
    imager: ImagerStatus | PHD2ImagerStatus | None = None
    covers: CoverStatus | None = None
    focuser: FocuserStatus | None = None
    stage: StageStatus | None = None
    guider: GuiderStatus | None = None
    errors: list[str] | None = None
    autofocus: dict | None = None
    corrections: list | None = None
    date: str | None = None
    powered: bool = True
    detected: bool = True

class UnitStatus(BaseStatus, PowerStatus):
    pass

class ControllerStatus(BaseStatus):
    operational: bool = True
    why_not_operational: list[str] | None = []


class GreateyesStatus(ComponentStatus):
    powered: bool = False
    band: str | None = None
    ipaddr: str | None = None
    enabled: bool = False
    connected: bool = False
    addr: int | None = None
    min_temp: float | None = None
    max_temp: float | None = None
    sensor_temperature_target: float | None = None
    sensor_temperature: float | None = None
    back_temperature: float | None = None
    errors: list[str] | None = None
    latest_exposure: Any = None
    latest_settings: Any = None


class DeepspecStatus(ComponentStatus):
    cameras: dict[str, GreateyesStatus] = {}


class NewtonStatus(ComponentStatus):
    powered: bool = False
    temperature: float | None = None
    errors: list[str] | None = None
    latest_exposure: Any = None
    latest_settings: Any = None


class QHY600Status(ComponentStatus):
    powered: bool = False
    temperature: float | None = None
    errors: list[str] | None = None
    latest_exposure: Any = None
    latest_settings: Any = None


class HighspecStatus(ComponentStatus):
    camera_type: str | None = None
    camera_status: NewtonStatus | QHY600Status | None = None


CalibrationLampStatus = BaseStatus


class WheelStatus(ComponentStatus):
    filters: dict[FilterPositions, str] = {}
    serial_number: str | None = None
    id: str | None = None
    position: int | None = None
    speed_mode: str | None = None
    sensor_mode: str | None = None
    current_filter: str | None = None


SpecStagePresets = dict[GratingNames | SpecInstruments, int]


class SpecStageStatus(ComponentStatus):
    presets: SpecStagePresets = {}
    position: int | None = None
    position_m: float | None = None
    position_cm: float | None = None
    position_mm: float | None = None
    position_um: float | None = None
    position_nm: float | None = None
    at_preset: str | None = None


class SpecStatus(BaseStatus):
    deepspec: DeepspecStatus | None = None
    highspec: HighspecStatus | None = None
    stages: dict[SpecStageNames, SpecStageStatus] | None = None
    chiller: BaseStatus | None = None
    lamps: dict[WheelNames, CalibrationLampStatus] | None = None
    wheels: dict[WheelNames, WheelStatus] | None = None


class SiteStatus(BaseModel):
    """Status of a controlled site."""

    controller: ControllerStatus | None = None
    units: dict[str, UnitStatus] | None = None
    spec: SpecStatus | None = None


class SitesStatus(BaseModel):
    """API response containing status of all controlled sites."""

    timestamp: str
    sites: dict[str, SiteStatus]


# # Example usage:
# if __name__ == "__main__":
#     import json

#     # Short status
#     basic = ShortUnitStatus(powered=True, detected=True, operational=True)
#     print(json.dumps(basic.model_dump(), indent=2))

#     # Full status
#     full = FullUnitStatus(
#         powered=True,
#         detected=True,
#         operational=False,
#         why_not_operational=["Mount not responding"],
#         activities_verbal=["Parking"],
#         mount_connected=False,
#         camera_connected=True
#     )
#     print(json.dumps(full.model_dump(), indent=2))

#     # Response with basic status
#     response1 = UnitStatusResponse(
#         unit_name="mastw",
#         timestamp="2024-12-03T18:00:00Z",
#         status=basic
#     )
#     print(json.dumps(response1.model_dump(), indent=2))

#     # Response with full status
#     response2 = UnitStatusResponse(
#         unit_name="mast01",
#         timestamp="2024-12-03T18:00:00Z",
#         status=full
#     )
#     print(json.dumps(response2.model_dump(), indent=2))
