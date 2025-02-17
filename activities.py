from enum import IntFlag, auto
from common.mast_logging import init_log
import datetime

import logging

logger = logging.Logger('mast.' + __name__)
init_log(logger)


class Timing:
    start_time: datetime.datetime
    end_time: datetime.datetime
    duration: datetime.timedelta

    def __init__(self):
        self.start_time = datetime.datetime.now()

    def end(self):
        self.end_time = datetime.datetime.now()
        self.duration = self.end_time - self.start_time


class Activities:
    """
    An activity consists of:
    * A flag bit (ON when the activity is in-progress, OFF when not)
    * A timing construct that monitors the start, end and duration of the activity

    The activity can be started, ended and checked if in-progress
    """

    Idle: IntFlag = 0

    def __init__(self):
        self.activities: IntFlag = Activities.Idle
        self.timings = dict()

    def start_activity(self, activity: IntFlag, existing_ok: bool = False):
        """
        Marks the start of an activity.
        :param activity:
        :param existing_ok: If already in progress don't create a new timing structure
        :return:
        """
        if existing_ok and (self.activities & activity) != 0:
            return

        self.activities |= activity
        self.timings[activity] = Timing()
        logger.info(f"started activity {activity.__repr__()}")

    def end_activity(self, activity: IntFlag):
        """
        Marks the end of an activity
        :param activity:
        :return:
        """
        if not self.is_active(activity):
            return
        self.activities &= ~activity
        self.timings[activity].end()
        logger.info(f"ended activity {activity.__repr__()}, duration={self.timings[activity].duration}")

    def is_active(self, activity):
        """
        Checks if an activity is active (in-progress)
        :param activity:
        :return:
        """
        return (self.activities & activity) != 0

    def is_idle(self):
        """
        Checks if no activities are in-progress
        :return: True if no in-progress activities, False otherwise
        """
        return self.activities == 0

    def __repr__(self):
        return self.activities.__repr__()


class UnitActivities(IntFlag):
    Idle = 0
    AutofocusingPWI4 = auto()
    Autofocusing = auto()
    AutofocusAnalysis = auto()
    Guiding = auto()
    StartingUp = auto()
    ShuttingDown = auto()
    Acquiring = auto()
    Positioning = auto()    # getting in position (e.g. for acquisition)
    Solving = auto()
    Correcting = auto()


class CameraActivities(IntFlag):
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
    Idle = 0
    StartingUp = auto()
    ShuttingDown = auto()
    Moving = auto()

class SpecActivities(IntFlag):
    StartingUp = auto()
    ShuttingDown = auto()
    Positioning = auto()
    Exposing = auto()

class DeepspecActivities(IntFlag):
    CoolingDown = auto()
    WarmingUp = auto()
    Exposing = auto()
    Positioning = auto()

class HighspecActivities(IntFlag):
    CoolingDown = auto()
    WarmingUp = auto()
    Exposing = auto()
    Focusing = auto()
    Positioning = auto()


class AssignmentActivities(IntFlag):
    Idle = auto()
    Probing = auto()
    Dispatching = auto()
    Aborting = auto()
    WaitingForGuiding = auto()
    ExposingSpec = auto()
    Executing = auto()
    WaitingForSpecDone = auto()
