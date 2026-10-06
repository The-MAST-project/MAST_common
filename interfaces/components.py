from abc import ABC, abstractmethod
from enum import IntFlag

from common.activities import Activities
from common.endpoints import Tier, endpoint
from common.models.statuses import ComponentStatus


class Component(ABC, Activities):
    """A hardware component of a unit or spec machine. Its lifecycle (opmode-design section 4a):

    - ``__init__()`` -- initialises the component's fields, powers it on and connects. It does
      NOT consult the opmode: only the top component (the unit or spec) reads it, and decides
      in ``start_lifespan`` whether to call ``startup()`` at once (``operated``) or wait for
      the command (``controlled``).
    - ``startup()`` -- tries to make the component operational; success is reported through
      ``operational`` / ``why_not_operational``.
    - ``shutdown()`` -- performs the component's shutdown activities, sets ``was_shut_down``, and
      leaves it powered, ready to ``startup()`` again or ``powerdown()`` -- unless the machine's
      ``power_down_on_shutdown`` is set, in which case it calls ``powerdown()`` itself.
    - ``powerdown()`` -- powers the component down.

    Components carry no ``opstate``; that is the top component's alone.
    """

    def __init__(self, activities_type: type[IntFlag]):
        Activities.__init__(self)
        self.activities: IntFlag = activities_type(0)

    @endpoint(tier=Tier.INTERFACE, methods=("PUT",))
    @abstractmethod
    def startup(self):
        """
        Called whenever an observing session starts (at sun-down or when safety returns)
        :return:
        """

    @endpoint(tier=Tier.INTERFACE, methods=("PUT",))
    @abstractmethod
    def shutdown(self):
        """
        Called whenever an observing session is terminated (at sun-up or when becoming unsafe)
        """

    @property
    @abstractmethod
    def is_shutting_down(self) -> bool:
        """
        Indicates whether the component is currently in the process of shutting down.
        This can be used by the controller to determine whether it needs to wait for
        shutdown to complete before starting up again.
        :return:
        """

    @abstractmethod
    def powerdown(self):
        pass

    @endpoint(tier=Tier.INTERFACE, methods=("PUT",))
    @abstractmethod
    def abort(self):
        """
        Immediately terminates any in-progress activities and returns the component to its
         default state.
        :return:
        """

    @endpoint(tier=Tier.INTERFACE, methods=("GET",))
    @abstractmethod
    def status(self):
        """
        Returns the component's current status
        :return:
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """The getter method for the abstract name property."""

    @name.setter
    @abstractmethod
    def name(self, value: str):
        """The setter method for the abstract name property."""

    @property
    @abstractmethod
    def operational(self) -> bool:
        """The getter method for the abstract name property."""

    @operational.setter
    @abstractmethod
    def operational(self, value: str) -> bool:
        """The setter method for the abstract name property."""

    @property
    @abstractmethod
    def why_not_operational(self) -> list[str]:
        pass

    @property
    @abstractmethod
    def detected(self) -> bool:
        pass

    @property
    @abstractmethod
    def connected(self) -> bool:
        pass

    @property
    @abstractmethod
    def was_shut_down(self) -> bool:
        pass

    @property
    def notification_path(self) -> list[str] | None:
        """
        The master status structure (common.models.statuses.SitesStatus) is hierarchical, e.g.:
            site[0]:
                ['units'][unit_name]: ... path to unit field ...
                ['spec']: ... path to site spec field ...
                ['controller']: ... path to site controller field ...
            site[1]:
                ...

        The GUI server caches a copy of this master status structure and serves it to clients on request.
        Notifications of field changes are sent to the GUI server with:
        - an initiator object that indicates which component is sending the
          notification (e.g. site: 'wis', 'units', 'mastw'), and
        - a 'notification_path' that indicates where in the master status structure the change occurred.

        Examples:
        - if the spec's 'G' camera's 'activities_verbal' property changed, the notification_path would be:
            ['deepspec', 'camera', 'G', 'activities_verbal']
        - if a unit's stage wants to notify about the current position and whether it is at a preset position,
            it will end two notifications with paths:
            ['stage', 'position']
            ['stage', 'at_preset']

        This property produces the list of keys into the master status dictionary where this notification is targeted.
        """
        return None

    def component_status(self) -> ComponentStatus:
        return ComponentStatus(
            detected=self.detected,
            connected=self.connected,
            activities=int(self.activities),
            activities_verbal=self.activities_verbal,
            operational=self.operational,
            why_not_operational=self.why_not_operational,
            was_shut_down=self.was_shut_down,
        )
