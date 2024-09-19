from enum import IntFlag, auto
from common.mast_logging import init_log
import datetime

import logging

logger = logging.Logger('mast.unit.activities')
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

    Idle: IntFlag = 0

    def __init__(self):
        self.activities: IntFlag = Activities.Idle
        self.timings = dict()

    def start_activity(self, activity: IntFlag):
        self.activities |= activity
        self.timings[activity] = Timing()
        logger.info(f"started activity {activity.__repr__()}")

    def end_activity(self, activity: IntFlag):
        if not self.is_active(activity):
            return
        self.activities &= ~activity
        self.timings[activity].end()
        logger.info(f"ended activity {activity.__repr__()}, duration={self.timings[activity].duration}")

    def is_active(self, activity):
        return (self.activities & activity) != 0

    def is_idle(self):
        return self.activities == 0

    def __repr__(self):
        return self.activities.__repr__()


class UnitActivities(IntFlag):
    Idle = 0
    AutofocusingPWI4 = auto()
    AutofocusingWIS = auto()
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


class StageActivities(IntFlag):
    Idle = 0
    StartingUp = auto()
    ShuttingDown = auto()
    Moving = auto()
