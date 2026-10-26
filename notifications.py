import asyncio
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from common.mast_logging import get_logger

logger = get_logger(__name__)
NotificationCardType = Literal["info", "warning", "error", "start", "end"]
NotificationTypes = Literal["ui_notification", "assignment_notification"]  # more to come
DomUpdateSpec = Literal["badge", "text"] | None


class NotificationInitiator(BaseModel):
    site: str = "unknown site"
    type: str = "unknown-machine-type"  # e.g., 'unit', 'controller', 'spec'
    hostname: str | None = None  # e.g., unit name, controller name, spec name
    project: str | None = None  # e.g., 'mast', 'past'


def _build_initiator() -> NotificationInitiator:
    """Identify THIS machine as a notification originator, from the config file.

    Built lazily (never at import time) so that a missing/invalid configuration
    surfaces at the application's startup fail-fast point rather than as an
    import error. Site and project come from the config file (the single source
    of truth); the machine type comes from the config file's `machine_role`.
    """
    from common.config.local import load_local_config

    local = load_local_config()
    role = local.machine_role  # validated to one of 'unit' | 'spec' | 'control'
    machine_type = {"unit": "unit", "spec": "spec", "control": "controller"}.get(role, "unknown-machine-type")
    return NotificationInitiator(
        site=local.site,
        type=machine_type,
        hostname=socket.gethostname().split(".")[0],
        project=local.project,
    )


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

    type: Literal["ui_notification"] = "ui_notification"
    initiator: NotificationInitiator = Field(default_factory=_build_initiator)  # The originator of the notification
    notifications: list[UiUpdateNotification] = []  # List of individual notification items


@dataclass
class _Pending:
    """One notification waiting to be sent."""

    data: str
    #: UI notifications with the same key replace each other while they wait: only the latest
    #: value of an element is worth showing. None: never replaced (a card is a message).
    key: tuple | None = None
    enqueued_at: float = field(default_factory=time.monotonic)


class Notifier:
    """Queues notifications to the control machine and sends them on its own thread.

    Two queues, because the two kinds are different things:

    - **Assignment notifications** are a handshake. The controller answers `ok` only once it
      has acted on one (or deliberately decided to ignore it), so anything else -- a refused
      connection, a timeout, an error -- leaves it at the head of its queue, to be sent again.
      Never evicted; first in, first out, one at a time, so a plan's `in-progress` always
      precedes its `completed`; always sent before any UI notification. Given up after
      ASSIGNMENT_MAX_AGE, with an ERROR: the plan's own backstop then decides
      (plans/control_acquisition_design.md 5.7).
    - **UI notifications** are ephemeral. Bounded; one waiting for the same elements as a newer
      one is replaced by it; the oldest goes when the queue is full. They wait while the
      control machine is unreachable and are sent when it is back.

    A failed send backs off, BACKOFF_FIRST doubling up to BACKOFF_MAX, and any success resets it.

    It used to be one queue of 10, where a burst of UI updates could push a plan's notification
    out before it was ever tried, and where a send that came back with errors -- `BaseApi.put`
    returns them, it does not raise -- was taken off the queue as though delivered.
    """

    _instance = None
    _initialized = False
    _init_lock = threading.Lock()

    NOTIFICATION_QUEUE_SIZE = 10  # UI notifications
    NOTIFICATION_TIMEOUT = 2.0  # UI notifications
    ASSIGNMENT_TIMEOUT = 10.0
    ASSIGNMENT_MAX_AGE = 600.0
    BACKOFF_FIRST = 1.0
    BACKOFF_MAX = 30.0

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        with Notifier._init_lock:
            if self._initialized:
                return

            self.lock = threading.Lock()

            # The two queues (see the class docstring) and the worker thread
            self.assignment_queue: deque[_Pending] = deque()
            self.notification_queue: deque[_Pending] = deque(maxlen=self.NOTIFICATION_QUEUE_SIZE)
            self.notification_event = threading.Event()
            self._backoff = 0.0
            self.stop_event = threading.Event()

            self.initiator = _build_initiator()
            # Built by the worker on its first send, never here: constructing it resolves the
            # control machine's name, and Notifier() runs on every start_activity's thread.
            self.notification_api = None
            self._unreachable = False

            # Start worker thread
            self.worker_thread = threading.Thread(
                target=self._notification_worker,
                name="NotificationWorker",
                daemon=True,
            )
            logger.debug(f"Starting notification worker thread: {id(self):x}")
            self.worker_thread.start()
            self._initialized = True

    def _notification_worker(self):
        """Send what is queued, assignment notifications first; back off on failure."""
        while not self.stop_event.is_set():
            if self._backoff:
                # After a failed send: wait out the back-off, whatever arrives meanwhile.
                if self.stop_event.wait(self._backoff):
                    break
            else:
                # Woken by a new notification (or every second, to be safe).
                self.notification_event.wait(timeout=1.0)
            while not self.stop_event.is_set():
                with self.lock:
                    self._give_up_on_stale_assignments()
                    queue = self.assignment_queue if self.assignment_queue else self.notification_queue
                    if not queue:
                        self.notification_event.clear()
                        break
                    pending = queue[0]
                if not self._send(pending, assignment=queue is self.assignment_queue):
                    self._backoff = min(self.BACKOFF_MAX, max(self.BACKOFF_FIRST, self._backoff * 2))
                    break
                self._backoff = 0.0
                with self.lock:
                    if queue and queue[0] is pending:
                        queue.popleft()

    def _send(self, pending: _Pending, assignment: bool) -> bool:
        """True when it went: for an assignment notification, the controller answered `ok`."""
        try:
            timeout = self.ASSIGNMENT_TIMEOUT if assignment else self.NOTIFICATION_TIMEOUT
            response = asyncio.run(self._api().put("notifications", data=pending.data, timeout=timeout))
        except Exception as e:  # noqa: BLE001 -- the sender thread must outlive any one failed notification
            self._report_reachability(e)
            return False
        if response.failed:
            self._report_reachability(response.errors)
            return False
        self._report_reachability(None)
        if assignment and response.value != "ok":
            logger.warning(
                f"notifications: the controller did not acknowledge an assignment notification: {response.value!r}"
            )
            return False
        return True

    def _give_up_on_stale_assignments(self) -> None:
        """Drop assignment notifications older than ASSIGNMENT_MAX_AGE (caller holds the lock)."""
        now = time.monotonic()
        while self.assignment_queue and now - self.assignment_queue[0].enqueued_at > self.ASSIGNMENT_MAX_AGE:
            stale = self.assignment_queue.popleft()
            logger.error(
                f"notifications: gave up on an assignment notification after {self.ASSIGNMENT_MAX_AGE:g} s "
                f"undelivered: {stale.data}"
            )

    def _api(self):
        if self.notification_api is None:
            from common.api import NotificationApi

            self.notification_api = NotificationApi(site_name=self.initiator.site)
        return self.notification_api

    def _report_reachability(self, failure):
        """Log the transitions only, so a long outage costs two lines rather than one per activity."""
        if failure is not None and not self._unreachable:
            logger.warning(
                f"notifications: cannot reach the control machine, notifications are not being delivered: {failure}"
            )
        elif failure is None and self._unreachable:
            logger.info("notifications: the control machine is reachable again")
        self._unreachable = failure is not None

    def _enqueue_notification(self, data: str, key: tuple | None = None):
        """Queue a UI notification, replacing one waiting for the same elements."""
        with self.lock:
            if key is not None:
                for i, waiting in enumerate(self.notification_queue):
                    if waiting.key == key:
                        del self.notification_queue[i]
                        break
            self.notification_queue.append(_Pending(data, key))
            self.notification_event.set()

    def _enqueue_assignment(self, data: str):
        """Queue an assignment notification: never evicted, sent before any UI one."""
        with self.lock:
            self.assignment_queue.append(_Pending(data))
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

        ui_update_request: UiUpdateNotifications = UiUpdateNotifications(type="ui_notification", initiator=self.initiator)

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

        # Replaceable by a newer one for the same elements, unless it carries a card: a card is
        # a message to show, not a value to bring up to date.
        key = None
        if all(n.card is None for n in ui_update_request.notifications):
            key = tuple(tuple(n.cache.path or []) for n in ui_update_request.notifications if n.cache is not None)
        self._enqueue_notification(ui_update_request.model_dump_json(), key)

    def assignment_notification(self, notification):
        """
        Sends an assignment notification to the controller.
        The initiator is always stamped from this process's NotificationInitiator.

        :param notification: AssignmentNotification instance (initiator will be overridden)
        """
        from common.models.assignments import AssignmentNotification

        assert isinstance(notification, AssignmentNotification)
        notification.initiator = self.initiator
        self._enqueue_assignment(notification.model_dump_json())


def __getattr__(name: str):
    # PEP 562: lazily expose module-level `initiator` so importing this module never
    # forces the configuration to load at import time. Callers do
    # `from common.notifications import initiator` (e.g. models.assignments) and get
    # a freshly built initiator on demand.
    if name == "initiator":
        return _build_initiator()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
