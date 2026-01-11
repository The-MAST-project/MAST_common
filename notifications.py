import asyncio
import logging
import socket
import threading
from collections import deque
from typing import Literal

from pydantic import BaseModel

from common.config import Config
from common.mast_logging import init_log

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
    machine: str = "unknown machine"

initiator: NotificationInitiator | None = None
if not initiator:
    local_site = Config().local_site
    local_site_name = local_site.name if local_site and local_site.name else 'unknown site'

    local_machine_name = socket.gethostname().split('.')[0]
    if local_machine_name.endswith('-spec'):
        local_machine_name = 'spec'
    elif local_machine_name.endswith('-control'):
        local_machine_name = 'controller'
    initiator = NotificationInitiator(site=local_site_name, machine=local_machine_name)

class Notification(BaseModel):
    """
    The GUI maintains a cached status of all the sites and machines controlled by the controller machine

    A Notification is used for:
    - Updating a cached status entry
    - Updating a GUI element with either one or more badges or some text:
      - Badges are mainly used for in-progress activities (e.g. "Focusing", "Slewing", etc)
      - Text may be used for updating a position value
    - If the card field is set, a notification card is displayed in the GUI with the specified type (infers icon) and details
    """
    initiator: NotificationInitiator = initiator
    path: list[str] | None = None
    value: list[str] | str | None = None
    ui_element: NotificationUIElement = 'text'
    card: NotificationCard | None = None

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

    def send(self, **notification):
        """Sends a notification asynchronously via the background worker thread"""

        path = notification.get('path')
        if not path:
            logger.error("Notifier.send: 'path' is required in notification_kwargs")
            return

        value = notification.get('value')
        if not value:
            logger.error("Notifier.send: 'value' is required in notification_kwargs")
            return

        ui_element = notification.get('ui_element', 'text')

        card = notification.get('card')
        if card and 'type' not in card:
            card['type'] = 'info'
            if 'message' not in card:
                card['message'] = 'Notification'
            if 'details' not in card:
                card['details'] = []

        notification = Notification(path=path, value=value, ui_element=ui_element, card=card)
        self._enqueue_notification(notification.model_dump_json())
