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

NotificationCardType = Literal['info', 'warning', 'error', 'start', 'end']
NotificationTypes = Literal["update"]  # more to come
DomUpdateSpec = Literal['badge', 'text'] | None

class NotificationInitiator(BaseModel):
    site: str = "unknown site"
    type: str = "unknown-machine-type"  # e.g., 'unit', 'controller', 'spec'
    hostname: str | None = None  # e.g., unit name, controller name, spec name
    project: str | None = None  # e.g., 'mast', 'past'
    component: str | None = None  # e.g., 'focuser', 'filter_wheel'

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
        type=local_machine_type,
        hostname=local_machine_name,
        project=local_project,
        component=None,
    )

    del local_site_name
    del local_machine_name
    del local_machine_type
    del local_project

#
# Specifications: allow sepcifying notification message contents
#

class CardUpdateSpec(BaseModel):
    """
    Allows initiator to request a card notification be displayed in the UI
    """
    type: NotificationCardType = 'info'  # 'info'|'error'|'warning'|'start'|'end'
    message: str | None = None
    details: list[str] = []
    duration: str | None = None  # For 'end' type cards

class UpdateSpec(BaseModel):
    """
    Specification for updating cache and/or DOM element
    """
    path: list[str]
    value: list[str] | str | int | float | bool | None = None  # The value being updated
    dom: DomUpdateSpec = None  # How to render the value(s) in the DOM element
    card: CardUpdateSpec | None = None  # Card notification specification

#
# Messages: actual notification messages sent to Django server
#
class DomMessage(BaseModel):
    """
    Dom information passed to the Django server for updating the UI
    """
    id: str  # DOM element ID
    render_as: DomUpdateSpec = 'text'  # How to render the value(s)

class CardMessage(BaseModel):
    """
    Allows initiator to request a card notification be displayed in the UI
    """
    type: NotificationCardType = 'info'  # 'info'|'error'|'warning'|'start'|'end'
    message: str | None = None
    details: list[str] = []
    duration: str | None = None  # For 'end' type cards

class CacheMessage(BaseModel):
    """
    Cache information passed to the Django server for display in the UI
    """
    path: str | None = None
    value: list[str] | str | int | float | bool | None = None  # The value being updated

    def model_post_init(self, context):
        return super().model_post_init(context)

class UpdateMessage(BaseModel):
    cache: CacheMessage | None = None  # Whether to update cache
    dom: DomMessage | None = None  # DOM update information
    card: CardMessage | None = None  # UI card information

class Update(BaseModel):
    """
    A status update notification request
    - produced when a notification is initiated, in units, controller or spec
    - sent to the controller fastapi notifications endpoint which relays it to the Django server
    - consumed by the Django server to update its cache
    - broadcasted to attached browsers to update DOM elements, and display notification cards
    """
    type: Literal["update"] = "update" # Type of notification
    initiator: NotificationInitiator = initiator  # The originator of the notification
    messages: list[UpdateMessage] = []  # List of individual notification items

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

    def update(self, specs: list[UpdateSpec] | UpdateSpec):
        """
        Asks for a notification to be sent to the Django server.
        - always updates the cache at 'path' with 'value'
        - optionally updates the DOM element with id derived from 'path' using 'dom' renderer
        - optionally displays a notification card using 'card' specification
        """
        if isinstance(specs, UpdateSpec):
            specs = [specs]

        update: Update = Update(
            type="update",
            initiator=self.initiator)

        for spec in specs:
            message = UpdateMessage()
            path = spec.path
            value = spec.value

            # cache update
            cache_path = [self.initiator.site]
            if self.initiator.type == 'unit':
                cache_path += ['unit']
            cache_path += [self.initiator.hostname] + path
            message.cache = CacheMessage(path=cache_path, value=value)

            # DOM update
            if spec.dom is not None:
                dom_id = "-".join(['id'] + path)
                message.dom = DomMessage(id=dom_id, render_as=spec.dom)

            # Card notification
            if spec.card is not None:
                message.card = CardMessage(
                    type=spec.card.type,
                    message=spec.card.message,
                    details=spec.card.details,
                    duration=spec.card.duration,
                )
            update.messages.append(message)

        self._enqueue_notification(update.model_dump_json())
