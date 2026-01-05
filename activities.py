import asyncio
import datetime
import logging
import socket
import threading
from collections import deque
from enum import IntFlag, auto

import humanfriendly
from pydantic import BaseModel

from common.mast_logging import init_log

logger = logging.getLogger("mast." + __name__)
init_log(logger)

hostname = socket.gethostname()


class ActivityNotification(BaseModel):
    initiator: str = hostname
    activity: int
    activity_verbal: str
    started: bool = False
    duration: str | None = None
    details: str | None = None


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

    Notifications are queued and sent asynchronously by a background worker thread.
    """

    NOTIFICATION_QUEUE_SIZE = 10
    NOTIFICATION_TIMEOUT = 2.0

    Idle = 0

    def __init__(self):
        from common.api import ControllerApi

        self.activities: IntFlag = Activity.Idle
        self.timings: dict[IntFlag, Timing] = {}
        self.details: dict[IntFlag, str] = {}
        self.lock = threading.Lock()

        # Notification queue and worker thread
        self.notification_queue = deque(maxlen=self.NOTIFICATION_QUEUE_SIZE)
        self.notification_event = threading.Event()
        self.stop_event = threading.Event()

        self.notification_client = ControllerApi(site_name="wis").client
        if self.notification_client:
            self.notification_client.timeout = self.NOTIFICATION_TIMEOUT

        # Start worker thread
        self.worker_thread = threading.Thread(
            target=self._notification_worker,
            name="ActivityNotificationWorker",
            daemon=True
        )
        self.worker_thread.start()

    def _notification_worker(self):
        """Background worker that sends queued notifications"""
        while not self.stop_event.is_set():
            # Wait for signal or timeout
            self.notification_event.wait(timeout=1.0)

            # Process all queued notifications
            while True:
                with self.lock:
                    if not self.notification_queue:
                        self.notification_event.clear()
                        break
                    data = self.notification_queue[0]

                # Try to send
                try:
                    if self.notification_client:
                        asyncio.run(
                            self.notification_client.put("activity_notification", data=data)
                        )
                    # Success - remove from queue
                    with self.lock:
                        if self.notification_queue and self.notification_queue[0] == data:
                            self.notification_queue.popleft()
                except Exception as e:
                    logger.error(f"Failed to send activity notification: {e}")
                    # Keep in queue for retry
                    break

    def _enqueue_notification(self, data: str):
        """Add notification to queue and signal worker if needed"""
        with self.lock:
            was_empty = len(self.notification_queue) == 0
            self.notification_queue.append(data)
            if was_empty:
                self.notification_event.set()

    def start_activity(
        self,
        activity: IntFlag,
        existing_ok: bool = False,
        label: str | None = None,
        details: str | None = None,
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
            info += f" details='{details}'"
        logger.info(info)

        data = ActivityNotification(
            activity=int(activity),
            activity_verbal=activity.__repr__(),
            started=True,
            details=details
        ).model_dump_json()
        self._enqueue_notification(data)

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
            info += f" details='{self.details[activity]}'"
            del self.details[activity]
        info += f", duration='{duration}'"
        logger.info(info)

        data = ActivityNotification(
            activity=int(activity),
            activity_verbal=activity.__repr__(),
            started=False,
            duration=duration,
        ).model_dump_json()
        self._enqueue_notification(data)

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
        """Gracefully shutdown the notification worker"""
        self.stop_event.set()
        self.notification_event.set()
        self.worker_thread.join(timeout=5.0)


    def activities_verbal(self) -> str:
        """
        Converts an activities IntFlag into a verbal string
        :param activities:
        :return:
        """
        if self.activities == 0:
            verbal = "Idle"
        else:
            verbal = self.activities.__repr__().rpartition(".")[2]
            verbal = verbal.partition(':')[0].replace('|', ', ')
        return verbal

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

class PowerSwitchActivities(IntFlag):
    Idle = 0

if __name__ == "__main__":
    a = Activities()
    a.start_activity(UnitActivities.Dancing, details="foxtrot")
    import time

    time.sleep(2)
    a.end_activity(UnitActivities.Dancing)
