import asyncio
import datetime
import logging
import socket
from enum import IntFlag, auto

import humanfriendly
from pydantic import BaseModel

from common.mast_logging import init_log

logger = logging.Logger("mast." + __name__)
init_log(logger)

hostname = None
if not hostname:
    hostname = socket.gethostname()


class ActivityNotification(BaseModel):
    initiator: str = hostname
    activity: int
    activity_verbal: str
    started: bool = False
    duration: str | None = None


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
        self.timings: dict[IntFlag, Timing] = {}

    async def notify_activity(self, data):
        from common.api import ControllerApi

        client = ControllerApi("wis").client
        if client:
            await client.put("activity_notification", data=data)

    def start_activity(
        self, activity: IntFlag, existing_ok: bool = False, label: str | None = None, details: str | None = None
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

        self.activities |= activity
        self.timings[activity] = Timing()
        info = ""
        if label:
            info += f"{label}: "
        info += f"started activity {activity.__repr__()}"
        if details:
            info += f" details='{details}'"
        logger.info(info)

        # data = ActivityNotification(
        #     activity=activity, activity_verbal=activity.__repr__(), started=True
        # ).model_dump_json()
        # try:
        #     loop = asyncio.get_event_loop()
        #     loop.create_task(self.notify_activity(data))
        # except RuntimeError:
        #     asyncio.run(self.notify_activity(data))

    def end_activity(self, activity: IntFlag, label: str | None = None):
        """
        Marks the end of an activity
        :param activity:
        :param label:
        :return:
        """
        if not self.is_active(activity):
            return
        self.activities &= ~activity

        if activity in self.timings:
            self.timings[activity].end()

        duration = humanfriendly.format_timespan(
            self.timings[activity].duration.total_seconds()
        )

        label = label + ": " if label else ""
        logger.info(
            f"{label}ended   activity {activity.__repr__()}, duration='{duration}'"
        )

        data = ActivityNotification(
            activity=int(activity),
            activity_verbal=activity.__repr__(),
            started=False,
            duration=duration,
        ).model_dump_json()

        try:
            loop = asyncio.get_event_loop()
            loop.create_task(self.notify_activity(data))
        except RuntimeError:
            asyncio.run(self.notify_activity(data))

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
    Positioning = auto()  # getting in position (e.g. for acquisition)
    Solving = auto()
    Correcting = auto()


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
    Idle = 0
    StartingUp = auto()
    ShuttingDown = auto()
    Moving = auto()
    Homing = auto()


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
    Focusing = auto()
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
