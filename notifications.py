import asyncio
import logging
import socket
import threading
from collections import deque
from typing import Literal

from pydantic import BaseModel

from common.config import Config
from common.mast_logging import init_log
from common.utils import function_name

logger = logging.getLogger("mast." + __name__)
init_log(logger)

NotificationUIElement = Literal['badge', 'text']
NotificationCardType = Literal['info', 'warning', 'error', 'start', 'end']

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

    local_machine_type = None
    local_machine_name = socket.gethostname().split('.')[0]
    if local_machine_name.startswith(local_project + '-') and local_machine_name.endswith('-spec'):
        local_machine_type = 'spec'
    elif local_machine_name.startswith(local_project + '-') and local_machine_name.endswith('-control'):
        local_machine_type = 'controller'
    elif local_machine_name.startswith(local_project):
        local_machine_type = 'unit'
    if not local_machine_type:
        raise Exception(f"{function_name()}: could not determine local machine type from hostname '{local_machine_name}'")

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

class NotificationUpdateData(BaseModel):
    """
    A status update notification used to update the cached status of a component at the specified path
    """
    initiator: NotificationInitiator = initiator  # The originator of the notification
    type: Literal["status_update"] = "status_update"
    value: list[str] | str | int | float | bool | None = None  # The value being updated
    cache: dict = {}  # Optional cache update information
    dom: dict = {}  # Optional DOM update information
    card: dict = {}  # Optional UI card

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
        self.initiator = initiator
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
                                "notifications", data=data
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

    def send_update(self, **notification):
        """
        Sends a notification_update asynchronously via the background worker thread
        Notification kwargs:
        - value: list[str] | str | number | bool - The value for the notification, used for updating cache/dom
        - path: list[str] - Component-relative path (e.g. ['focuser', 'position'])
        - update_cache: bool | None - Optional cache update information
        - update_dom_as: 'badge' | 'text' = 'text' - How to render the value(s)
        - update_card: dict | None - Optional card information for the notification
          - type: 'info' | 'warning' | 'error' | 'start' | 'end'
          - message: str - The main message for the card
          - details: list[str] - Optional detailed messages
          - duration: str - Optional duration string (for 'end' type cards)
        """
        op = function_name()

        path = notification.get('path')
        value = notification.get('value')
        update_cache = notification.get('update_cache')
        update_dom_as = notification.get('update_dom_as')

        data: NotificationUpdateData = NotificationUpdateData(
            type="status_update",
            initiator=self.initiator)

        # Add value if provided
        if value is not None:
            data.value = value

        if update_cache and value is not None and path is not None:

            assert self.initiator is not None
            match self.initiator.machine_type:
                case 'unit':
                    data.cache["path"] = [self.initiator.site, 'unit', self.initiator.machine_name] + path
                case 'controller':
                    data.cache["path"] = [self.initiator.site, 'controller'] + path
                case 'spec':
                    data.cache["path"] = [self.initiator.site, 'spec'] + path
                case _:
                    raise Exception(f"{op}: Unknown initiator machine_type '{self.initiator.machine_type}'")

        if update_dom_as is not None and value is not None and path is not None:
            data.dom = {}
            data.dom['id'] = "-".join(['id'] + path)
            data.dom["render_as"] = update_dom_as

        update_card = notification.get('update_card')
        if update_card:

            data.card['type'] = update_card.get('type', 'info')
            if update_card.get('type') == 'end':
                data.card['duration'] = update_card.get('duration', '')
            data.card['message'] = update_card.get('message', 'Notification')
            data.card['details'] = update_card.get('details', [])

        self._enqueue_notification(data.model_dump_json())
