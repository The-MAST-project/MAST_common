import datetime
import logging
import socket
import threading
from enum import IntFlag, auto

import humanfriendly

from common.mast_logging import init_log
from common.notifications import CardUpdateSpec, Notifier, UiUpdateSpec

# from src.common.utils import function_name

logger = logging.getLogger("mast." + __name__)
init_log(logger)

hostname = socket.gethostname()

ActivitiesVerbal = list[str] | None


class Timing:
    start_time: datetime.datetime
    end_time: datetime.datetime
    duration: datetime.timedelta

    def __init__(self):
        self.start_time = datetime.datetime.now()

    def end(self):
        self.end_time = datetime.datetime.now()
        self.duration = self.end_time - self.start_time


class Activity(IntFlag):
    Idle = 0


class Activities:
    """
    An activity consists of:
    * A flag bit (ON when the activity is in-progress, OFF when not)
    * A timing construct that monitors the start, end and duration of the activity

    The activity can be started, ended and checked if in-progress
    """

    Idle = 0

    def __init__(self):
        self.activities: IntFlag = Activity.Idle
        self.timings: dict[IntFlag, Timing] = {}  # keyed on activity
        self.details: dict[IntFlag, list[str] | None] = {}  # keyed on activity
        self.lock = threading.Lock()

    @property
    def activities_type_to_component(self) -> str | None:
        """
        Converts an activity type to a component name
        :param activity_type:
        :return:
        """

        match type(self.activities).__name__:
            case "UnitActivities":
                return "unit"
            case "FocuserActivities":
                return "focuser"
            case "ImagerActivities":
                return "imager"
            case "CoverActivities":
                return "covers"
            case "MountActivities":
                return "mount"
            case "StageActivities":
                return "stage"
            case "DeepspecActivities":
                return "deepspec"
            case "HighspecActivities":
                return "highspec"
            case "PHD2Activities":
                return "phd2"
            case "GreatEyesActivities":
                return "greateyes"
            case "CalibrationLampActivities":
                return "calibration-lamp"
            case _:
                logger.error(
                    f"Unknown activities type '{type(self.activities).__name__}'"
                )
                return "unknown-component"

    @property
    def activities_type_to_notification_path(self) -> list[str]:
        """
        Converts an activity type to a notification path
        :param activity_type:
        :return:
        """

        component = None
        match type(self.activities).__name__:
            case "UnitActivities":
                return ["activities_verbal"]
            case "FocuserActivities":
                return ["focuser", "activities_verbal"]
            case "ImagerActivities":
                return ["imager", "activities_verbal"]
            case "CoverActivities":
                return ["covers", "activities_verbal"]
            case "MountActivities":
                return ["mount", "activities_verbal"]
            case "StageActivities":
                return ["stage", "activities_verbal"]
            case "DeepspecActivities":
                return ["deepspec", "activities_verbal"]
            case "HighspecActivities":
                return ["highspec", "activities_verbal"]
            case "PHD2Activities":
                return ["imager", "activities_verbal"]
            case "GreatEyesActivities":
                return ["deepspec", "greateyes", "activities_verbal"]
            case _:
                component = "unknown-component"
                # logger.error(f"{function_name()}: Unknown activities type '{type(self.activities).__name__}'")

        return [component] + ["activities"] if component else ["activities"]

    def start_activity(
        self,
        activity: IntFlag,
        existing_ok: bool = False,
        label: str | None = None,
        details: list[str] | None = None,
    ):
        """
        Marks the start of an activity.
        :param activity:
        :param existing_ok: If already in progress don't create a new timing structure
        :param label: Optional label to prefix to log message
        :return:
        """
        if existing_ok and (self.activities & activity) != 0:
            return

        with self.lock:
            self.activities |= activity
        self.timings[activity] = Timing()
        info = ""
        if label:
            info += f"{label}: "
        info += f"started activity {activity.__repr__()}"
        if details:
            self.details[activity] = details
            info += f" details={details}"
        logger.info(info)

        details = self.details.get(activity, None)
        if details is not None and not isinstance(details, list):
            details = [str(details)]
        Notifier().ui_update(
            UiUpdateSpec(
                path=self.activities_type_to_notification_path,
                value=self.activities_verbal,
                dom="badge",
                card=CardUpdateSpec(
                    component=self.activities_type_to_component,
                    type="start",
                    message=f"Started {activity._name_}",
                    details=details,
                ),
            )
        )

    def end_activity(self, activity: IntFlag, label: str | None = None):
        """
        Marks the end of an activity
        :param activity:
        :param label:
        :return:
        """
        if not self.is_active(activity):
            return
        with self.lock:
            self.activities &= ~activity

        if activity not in self.timings:
            logger.warning(f"Cannot end activity {activity}: timing not found.")
            return

        self.timings[activity].end()

        duration = humanfriendly.format_timespan(
            self.timings[activity].duration.total_seconds()
        )

        label = label + ": " if label else ""
        info = f"{label}ended   activity {activity.__repr__()}"
        if self.details.get(activity):
            info += f" details={self.details[activity]}"
            del self.details[activity]
        info += f", duration='{duration}'"
        logger.info(info)

        Notifier().ui_update(
            UiUpdateSpec(
                path=self.activities_type_to_notification_path,
                value=self.activities_verbal,
                dom="badge",
                card=CardUpdateSpec(
                    component=self.activities_type_to_component,
                    type="end",
                    message=f"Ended {activity._name_}",
                    details=self.details.get(activity, []),
                    duration=duration,
                ),
            )
        )

    def is_active(self, activity):
        """
        Checks if an activity is active (in-progress)
        :param activity:
        :return:
        """
        with self.lock:
            bits = self.activities & activity
        return bits != 0

    def is_idle(self):
        """
        Checks if no activities are in-progress
        :return: True if no in-progress activities, False otherwise
        """
        with self.lock:
            idle = self.activities == 0
        return idle

    def shutdown(self):
        pass

    @property
    def activities_verbal(self) -> ActivitiesVerbal:
        """
        Converts the activities IntFlag into a list of strings
        """
        if self.activities == 0:
            return None

        ret = self.activities.__repr__().rpartition(".")[2]
        ret = ret.partition(":")[0].split("|")
        return ret


class UnitActivities(IntFlag):
    Idle = 0
    AutofocusingPWI4 = auto()
    Autofocusing = auto()
    AutofocusAnalysis = auto()
    PreGuiding = auto()  # getting ready for guiding
    Guiding = auto()
    StartingUp = auto()
    ShuttingDown = auto()
    Acquiring = auto()
    Positioning = auto()  # getting in position (e.g. for acquisition)
    Solving = auto()
    Correcting = auto()
    SequenceOfExposures = auto()
    Dancing = auto()


class ImagerActivities(IntFlag):
    Idle = 0
    CoolingDown = auto()
    WarmingUp = auto()
    Exposing = auto()
    ShuttingDown = auto()
    StartingUp = auto()
    ReadingOut = auto()
    Saving = auto()


class CoverActivities(IntFlag):
    Idle = 0
    Opening = auto()
    Closing = auto()
    StartingUp = auto()
    ShuttingDown = auto()


class FocuserActivities(IntFlag):
    Idle = 0
    Moving = auto()
    StartingUp = auto()
    ShuttingDown = auto()


class MountActivities(IntFlag):
    StartingUp = auto()
    ShuttingDown = auto()
    Slewing = auto()
    Parking = auto()
    Tracking = auto()
    FindingHome = auto()
    Dancing = auto()
    Moving = auto()


class StageActivities(IntFlag):
    Homing = auto()
    Moving = auto()
    StartingUp = auto()
    ShuttingDown = auto()
    Aborting = auto()


class SpecActivities(IntFlag):
    StartingUp = auto()
    ShuttingDown = auto()
    Positioning = auto()
    Exposing = auto()


class DeepspecActivities(IntFlag):
    CoolingDown = auto()
    WarmingUp = auto()
    Acquiring = auto()
    Positioning = auto()


class HighspecActivities(IntFlag):
    CoolingDown = auto()
    WarmingUp = auto()
    Exposing = auto()
    AutoFocusing = auto()
    Positioning = auto()
    Acquiring = auto()


class AssignmentActivities(IntFlag):
    Idle = auto()
    Probing = auto()
    Dispatching = auto()
    Aborting = auto()
    WaitingForGuiding = auto()
    ExposingSpec = auto()
    Executing = auto()
    WaitingForSpecDone = auto()


class PowerSwitchActivities(IntFlag):
    Idle = 0


class ControllerActivities(IntFlag):
    Idle = 0
    Controlling = auto()
    Executing = auto()
    StartingUp = auto()
    ShuttingDown = auto()


class ControlledUnitActivities(IntFlag):
    Idle = 0


class GreatEyesActivities(IntFlag):
    CoolingDown = auto()
    WarmingUp = auto()
    AdjustingTemperature = auto()
    Acquiring = auto()
    Exposing = auto()
    ReadingOut = auto()
    Saving = auto()
    StartingUp = auto()
    ShuttingDown = auto()
    SettingParameters = auto()
    Probing = auto()


class CalibrationLampActivities(IntFlag):
    Idle = 0


if __name__ == "__main__":
    a = Activities()
    a.start_activity(UnitActivities.Dancing, details=["foxtrot"])
    import time

    time.sleep(2)
    a.end_activity(UnitActivities.Dancing)
