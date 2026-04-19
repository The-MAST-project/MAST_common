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

NotificationCardType = Literal["info", "warning", "error", "start", "end"]
NotificationTypes = Literal[
    "ui_notification", "assignment_notification"
]  # more to come
DomUpdateSpec = Literal["badge", "text"] | None


class NotificationInitiator(BaseModel):
    site: str = "unknown site"
    type: str = "unknown-machine-type"  # e.g., 'unit', 'controller', 'spec'
    hostname: str | None = None  # e.g., unit name, controller name, spec name
    project: str | None = None  # e.g., 'mast', 'past'


initiator: NotificationInitiator | None = None
if not initiator:
    sites = Config().get_sites()

    local_machine_name = socket.gethostname().split(".")[0]
    parts = local_machine_name.split("-")
    if len(parts) == 3:
        # mast-wis-spec, mast-ns-control, etc.
        local_project = parts[0]
        local_machine_type = (
            "spec"
            if parts[2] == "spec"
            else "controller"
            if parts[2] == "control"
            else "unknown-machine-type"
        )
        local_site_name = parts[1]
    elif len(parts) == 1:
        # mastw, mast00, mast12, etc.
        local_machine_type = "unit"
        local_site = [s for s in sites if local_machine_name in s.unit_ids]
        if local_site:
            local_site_name = local_site[0].name
            local_project = local_site[0].project
        else:
            raise Exception(
                f"{function_name()}: could not determine local site from hostname '{local_machine_name}'"
            )

    initiator = NotificationInitiator(
        site=local_site_name,
        type=local_machine_type,
        hostname=local_machine_name,
        project=local_project,
    )

    del local_site_name
    del local_machine_name
    del local_machine_type
    del local_project
    del sites

#
# Specifications: allow sepcifying notification message contents
#


class CardUpdateSpec(BaseModel):
    """
    Allows initiator to request a card notification be displayed in the UI
    """

    component: str | None = None
    type: NotificationCardType = "info"  # 'info'|'error'|'warning'|'start'|'end'
    message: str | None = None
    details: list[str] | None = None
    duration: str | None = None  # Human-readable duration for 'end' type cards
    data: dict | None = None  # Machine-readable payload (e.g. motion target)


class UiUpdateSpec(BaseModel):
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
class UiDomNotification(BaseModel):
    """
    Dom information passed to the Django server for updating the UI
    """

    id: str  # DOM element ID
    render_as: DomUpdateSpec = "text"  # How to render the value(s)


class UiCardNotification(BaseModel):
    """
    Allows initiator to request a card notification be displayed in the UI
    """

    type: NotificationCardType = "info"  # 'info'|'error'|'warning'|'start'|'end'
    message: str | None = None
    details: list[str] | None = None
    duration: str | None = None  # For 'end' type cards
    component: str | None = None
    data: dict | None = None  # Machine-readable payload (e.g. motion target)


class UiCacheNotification(BaseModel):
    """
    Cache information passed to the Django server for display in the UI
    """

    path: list[str] | None = None
    value: list[str] | str | int | float | bool | None = None  # The value being updated

    def model_post_init(self, context):
        return super().model_post_init(context)


class UiUpdateNotification(BaseModel):
    cache: UiCacheNotification | None = None  # Whether to update cache
    dom: UiDomNotification | None = None  # DOM update information
    card: UiCardNotification | None = None  # UI card information


class UiUpdateNotifications(BaseModel):
    """
    A status update notification request
    - produced when a notification is initiated, in units, controller or spec
    - sent to the controller fastapi notifications endpoint which relays it to the Django server
    - consumed by the Django server to update its cache
    - broadcasted to attached browsers to update DOM elements, and display notification cards
    """

    type: NotificationTypes = "ui_notification"  # Type of notification
    initiator: NotificationInitiator = initiator  # The originator of the notification
    notifications: list[
        UiUpdateNotification
    ] = []  # List of individual notification items


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
        self.notification_api = ControllerApi(site_name=initiator.site)
        if self.notification_api:
            self.notification_api.timeout = self.NOTIFICATION_TIMEOUT

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

                # Log what we're sending
                # logger.debug(f"Attempting to send notification: {data[:200]}...")

                # Try to send
                try:
                    if self.notification_api:
                        asyncio.run(
                            self.notification_api.put("notifications", data=data)
                        )
                    # Success - remove from queue
                    with self.lock:
                        if (
                            self.notification_queue
                            and self.notification_queue[0] == data
                        ):
                            self.notification_queue.popleft()
                            # logger.debug("Notification sent successfully")
                except Exception:
                    # logger.error(f"Failed to send notification: {e}")
                    # logger.error(
                    #     f"Data type: {type(data)}, length: {len(data) if isinstance(data, str) else 'N/A'}"
                    # )
                    # Keep in queue for retry
                    break

    def _enqueue_notification(self, data: str):
        """Add notification to queue and signal worker if needed"""
        with self.lock:
            was_empty = len(self.notification_queue) == 0
            self.notification_queue.append(data)
            if was_empty:
                self.notification_event.set()

    def ui_notification(self, ui_specs: list[UiUpdateSpec] | UiUpdateSpec):
        """
        Asks for a notification to be sent to the Django server.
        - always updates the cache at 'path' with 'value'
        - optionally updates the DOM element with id derived from 'path' using 'dom' renderer
        - optionally displays a notification card using 'card' specification

        Example usages:
        - unit mast00 (at Neot Smadar) wants to update the stage's position and preset name, it will send two ui_specs:
          - both with initiator.site = 'ns' and initiator.hostname = 'mast00'
          - one with path=['stage', 'position'], value being the current stage position and dom='text' to render it
             as text in the DOM element with id 'id-stage-position'
          - and one with path=['stage', 'preset'], value being the current preset name, and dom='badge' to render it
             as text in the DOM element with id 'id-stage-preset' with a badge style

        - the camera at unit mastw (at Weizmann) starts a CameraActivities.CoolingDown activity:
          - it will send one ui_spec with
          - initiator.site = 'wis'
          - initiator.hostname = 'mastw'
          - path=['camera', 'activities'] and value=['CoolingDown', 'StartingUp'], dom='badge' to update the cache and
            render it as badges in the DOM element with id 'id-camera-activities'
          - card specification with type='info', message='Camera is cooling down', and component='CameraActivityCard' to
             display an informational card in the UI

        """
        if isinstance(ui_specs, UiUpdateSpec):
            ui_specs = [ui_specs]

        ui_update_request: UiUpdateNotifications = UiUpdateNotifications(
            type="ui_notification", initiator=self.initiator
        )

        for ui_spec in ui_specs:
            message = UiUpdateNotification()
            path = ui_spec.path
            value = ui_spec.value

            # cache update
            cache_path: list[str] = [self.initiator.site]
            if self.initiator.type == "unit":
                cache_path += ["unit"]
            assert self.initiator.hostname is not None
            cache_path += [self.initiator.hostname] + path
            message.cache = UiCacheNotification(path=cache_path, value=value)

            # DOM update
            if ui_spec.dom is not None:
                dom_id = "-".join(["id"] + path)
                message.dom = UiDomNotification(id=dom_id, render_as=ui_spec.dom)

            # Card notification
            if ui_spec.card is not None:
                message.card = UiCardNotification(
                    type=ui_spec.card.type,
                    message=ui_spec.card.message,
                    details=ui_spec.card.details,
                    duration=ui_spec.card.duration,
                    component=ui_spec.card.component,
                )

            # logger.debug(f"Notifier.ui_notification: message={message.model_dump_json()}")
            ui_update_request.notifications.append(message)

        self._enqueue_notification(ui_update_request.model_dump_json())

    def assignment_notification(self, assignment_spec: dict):
        """
        Sends an assignment notification to the controller machine.
        - used for notifying about assignment status and details, e.g. resources allocated
        - the exact content of the assignment_spec is TBD but it should include at least:
          - plan ULID, if this assignment is related to a specific plan
          - batch ULID, if this assignment is related to a specific batch
          - assigned resources (e.g., path to results)
        """
        # For now we just log the assignment notification request
        logger.info(f"Received assignment notification request: {assignment_spec}")
        # In the future, we would construct an AssignmentUpdateRequest similar to UiUpdateRequest and
