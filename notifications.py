import asyncio
import logging
import socket
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from common.config import Config
from common.mast_logging import init_log
from common.utils import function_name, isoformat_zulu

logger = logging.getLogger("mast." + __name__)
init_log(logger)

NotificationUIElement = Literal['badge', 'text']
NotificationCardType = Literal['info', 'warning', 'error', 'start', 'end']

class NotificationCard(BaseModel):
    type: NotificationCardType = 'info'
    message: str | None = None
    details: list[str] | None = None
    duration: str | None

class NotificationInitiator(BaseModel):
    site: str = "unknown site"
    machine_type: str = "unknown-machine-type"  # e.g., 'unit', 'controller', 'spec'
    machine_name: str | None = None  # e.g., unit name, controller name, spec name
    project: str | None = None  # e.g., 'mast', 'past'

initiator: NotificationInitiator | None = None
if not initiator:
    local_site = Config().local_site
    local_site_name = local_site.name if local_site and local_site.name else 'unknown site'
    local_project = local_site.project if local_site and local_site.project else 'unknown project'

    local_machine_name = socket.gethostname().split('.')[0]
    if local_machine_name.startswith(local_project + '-') and local_machine_name.endswith('-spec'):
        local_machine_type = 'spec'
    elif local_machine_name.startswith(local_project + '-') and local_machine_name.endswith('-control'):
        local_machine_type = 'controller'
    elif local_machine_name.startswith(local_project):
        local_machine_type = 'unit'

    initiator = NotificationInitiator(
        site=local_site_name,
        machine_type=local_machine_type,
        machine_name=local_machine_name,
        project=local_project,
    )
    
    del local_site_name
    del local_machine_name
    del local_machine_type
    del local_project

NotificationTypes = Literal["status_update"]  # more to come

class Notification(BaseModel):
    """
    The GUI maintains a cached status of all the sites and machines controlled by the controller machine

    A Notification is used for:
    - Updating a cached status entry at the specified path
    - Updating a GUI element (the value can be list[str] for badges or simple value for text)
    - Optionally displaying a notification card (via 'card' field)
    """
    type: NotificationTypes
    path: list[str]
    value: list[str] | str | int | float | bool | None
    badges: bool = False  # Whether to display as badges (default False)
    timestamp: str | None = None
    initiator: NotificationInitiator = initiator  # Optional metadata
    card: NotificationCard | None = None  # Optional UI card

    def model_post_init(self, __context):
        """Set timestamp if not provided"""
        if self.timestamp is None:
            self.timestamp = isoformat_zulu()

class Notifier:
    _instance = None
    _initialized = False

    NOTIFICATION_QUEUE_SIZE = 10
    NOTIFICATION_TIMEOUT = 2.0

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        from common.api import ControllerApi

        self.lock = threading.Lock()
        self.controller_api = ControllerApi()

        # Notification queue and worker thread
        self.notification_queue = deque(maxlen=self.NOTIFICATION_QUEUE_SIZE)
        self.notification_event = threading.Event()
        self.stop_event = threading.Event()

        assert initiator is not None
        self.notification_client = ControllerApi(site_name=initiator.site).client
        if self.notification_client:
            self.notification_client.timeout = self.NOTIFICATION_TIMEOUT

        # Start worker thread
        self.worker_thread = threading.Thread(
            target=self._notification_worker,
            name="NotificationWorker",
            daemon=True,
        )
        self.worker_thread.start()
        self._initialized = True

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
                            self.notification_client.put(
                                "notification", data=data
                            )
                        )
                    # Success - remove from queue
                    with self.lock:
                        if (
                            self.notification_queue
                            and self.notification_queue[0] == data
                        ):
                            self.notification_queue.popleft()
                except Exception as e:
                    logger.error(f"Failed to send notification: {e}")
                    # Keep in queue for retry
                    break

    def _enqueue_notification(self, data: str):
        """Add notification to queue and signal worker if needed"""
        with self.lock:
            was_empty = len(self.notification_queue) == 0
            self.notification_queue.append(data)
            if was_empty:
                self.notification_event.set()

    def send_update(self, **notification_kwargs):
        """
        Sends a status_update notification asynchronously via the background worker thread
        Notification kwargs:
        - path: list[str] - Component-relative path (e.g. ['focuser', 'position'])
        - value: list[str] | str | number | bool - The value for the notification
        - badges: bool - Whether to display as badges (default False)
        - card: dict | None - Optional card information for the notification
        """
        op = function_name()

        type = "status_update"
        path = notification_kwargs.get('path')
        if not path:
            logger.error(f"{op}: 'path' is required in notification_kwargs")
            return
        
        # Prepend site and machine info to path
        match initiator.machine_type:
            case 'unit':
                path = [initiator.site, 'unit', initiator.machine_name] + path
            case 'controller':
                path = [initiator.site, 'controller'] + path
            case 'spec':
                path = [initiator.site, 'spec'] + path
            case _:
                logger.warning(f"{op}: Unknown initiator machine_type '{initiator.machine_type}'")

        value = notification_kwargs.get('value')
        if value is None:
            logger.error(f"{op}: 'value' is required in notification_kwargs")
            return

        badges = notification_kwargs.get('badges', False)

        card = notification_kwargs.get('card')
        if card and 'type' not in card:
            card['type'] = 'info'
            if card['type'] == 'end' and 'duration' not in card:
                card['duration'] = 'unknown'
            if 'message' not in card:
                card['message'] = 'Notification'
            if 'details' not in card:
                card['details'] = []

        notification = Notification(type=type, path=path, value=value, badges=badges, card=card)
        self._enqueue_notification(notification.model_dump_json())
