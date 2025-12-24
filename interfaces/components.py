from abc import ABC, abstractmethod

from pydantic import BaseModel

from common.activities import Activities, verbalize


class ComponentStatus(BaseModel):
    detected: bool = False
    connected: bool = False
    activities: int = 0
    activities_verbal: str | None = None
    operational: bool = False
    why_not_operational: list[str] = []
    was_shut_down: bool = False
    model_config = {"arbitrary_types_allowed": True}


class Component(ABC, Activities):
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

    def component_status(self) -> ComponentStatus:

        return ComponentStatus(
            detected=self.detected,
            connected=self.connected,
            activities=int(self.activities),
            activities_verbal=verbalize(self.activities),
            operational=self.operational,
            why_not_operational=self.why_not_operational,
            was_shut_down=self.was_shut_down,
        )
