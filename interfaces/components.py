from abc import ABC, abstractmethod

from pydantic import BaseModel

from common.activities import Activities, ActivitiesVerbal


class ComponentStatus(BaseModel):
    detected: bool = False
    connected: bool = False
    activities: int = 0
    activities_verbal: ActivitiesVerbal = None
    operational: bool = False
    why_not_operational: list[str] = []
    was_shut_down: bool = False
    model_config = {"arbitrary_types_allowed": True}


class Component(ABC, Activities):

    def __init__(self, activities_type):
        Activities.__init__(self)
        self.activities = activities_type(0)

    @abstractmethod
    def startup(self):
        """
        Called whenever an observing session starts (at sun-down or when safety returns)
        :return:
        """
        pass

    @abstractmethod
    def shutdown(self):
        """
        Called whenever an observing session is terminated (at sun-up or when becoming unsafe)
        :return:
        """
        pass

    @abstractmethod
    def abort(self):
        """
        Immediately terminates any in-progress activities and returns the component to its
         default state.
        :return:
        """
        pass

    @abstractmethod
    def status(self):
        """
        Returns the component's current status
        :return:
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """The getter method for the abstract name property."""
        pass

    @name.setter
    @abstractmethod
    def name(self, value: str):
        """The setter method for the abstract name property."""
        pass

    @property
    @abstractmethod
    def operational(self) -> bool:
        """The getter method for the abstract name property."""
        pass

    @operational.setter
    @abstractmethod
    def operational(self, value: str) -> bool:
        """The setter method for the abstract name property."""
        pass

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
        - an initiator object that indicates which component is sending the notification (e.g. site: 'wis', 'units', 'mastw'), and
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
