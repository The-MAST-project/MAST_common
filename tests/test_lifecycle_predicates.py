"""The two halves of `operational`, and the state they describe (MAST_unit#144).

`operational` conflates being *reachable* -- powered, enumerated, connected, the driver
answering -- with being *deployed*, the end state of a commanded motion. A machine that has
connected to everything and commanded nothing therefore reports `operational: false` with
reasons that read like faults, which is character-for-character what a machine with broken
hardware reports. Splitting the predicate is what makes the two distinguishable.

Two properties are worth pinning here. The halves are **opt-in**: a component that has not
adopted them keeps answering `operational` as it always did and reports `None`, so the ABC
change cannot break an unmigrated implementation in another repo. And the aggregation is
**weakest-wins**, because a machine is only as usable as its least usable required part.
"""

from __future__ import annotations

from enum import IntFlag, auto

from common.dlipowerswitch import DliPowerSwitch
from common.interfaces.components import Component
from common.models.statuses import (
    BaseStatus,
    ComponentStatus,
    LifecycleState,
    aggregate_lifecycle_state,
    component_lifecycle_state,
)


class FakeActivities(IntFlag):
    Idle = 0
    Moving = auto()


class Unmigrated(Component):
    """A component as they all are today: `operational` computed, the halves unanswered."""

    def __init__(self, operational: bool = True):
        Component.__init__(self, FakeActivities)
        self._operational = operational

    def startup(self):
        pass

    def shutdown(self):
        pass

    @property
    def is_shutting_down(self) -> bool:
        return False

    def powerdown(self):
        pass

    def abort(self):
        pass

    def status(self):
        return ComponentStatus()

    @property
    def name(self) -> str:
        return "unmigrated"

    @name.setter
    def name(self, value: str):
        pass

    @property
    def operational(self) -> bool:
        return self._operational

    @operational.setter
    def operational(self, value):
        self._operational = value

    @property
    def why_not_operational(self) -> list[str]:
        return [] if self._operational else ["unmigrated: no"]

    @property
    def detected(self) -> bool:
        return True

    @property
    def connected(self) -> bool:
        return True

    @property
    def was_shut_down(self) -> bool:
        return False


class Migrated(Unmigrated):
    """A component that answers the halves and states the conjunction itself."""

    def __init__(self, reachable: bool, deployed: bool):
        Unmigrated.__init__(self)
        self._reachable = reachable
        self._deployed = deployed

    @property
    def reachable(self) -> bool | None:
        return self._reachable

    @property
    def deployed(self) -> bool | None:
        return self._deployed

    @property
    def why_not_reachable(self) -> list[str] | None:
        return [] if self._reachable else ["migrated: not connected"]

    @property
    def why_not_deployed(self) -> list[str] | None:
        return [] if self._deployed else ["migrated: not at a preset"]

    @property
    def operational(self) -> bool:
        return bool(self.reachable) and bool(self.deployed)

    @property
    def why_not_operational(self) -> list[str]:
        return list(self.why_not_reachable or []) + list(self.why_not_deployed or [])


def test_an_unmigrated_component_answers_none_rather_than_false():
    """`None` is not `False`: it means the component does not report the halves at all."""
    component = Unmigrated(operational=False)

    assert component.reachable is None
    assert component.deployed is None
    assert component.why_not_reachable is None
    assert component.why_not_deployed is None
    assert component.operational is False


def test_the_default_halves_do_not_call_operational():
    """The ABC derives nothing, so a default and an override cannot recurse into each other."""
    component = Unmigrated(operational=True)
    component.operational = True

    assert component.reachable is None


def test_a_migrated_component_reports_both_halves_and_their_conjunction():
    assert Migrated(reachable=True, deployed=True).operational is True
    assert Migrated(reachable=True, deployed=False).operational is False
    assert Migrated(reachable=False, deployed=False).operational is False

    reachable_only = Migrated(reachable=True, deployed=False)
    assert reachable_only.why_not_operational == ["migrated: not at a preset"]
    assert reachable_only.why_not_reachable == []


def test_the_four_cells():
    assert component_lifecycle_state(reachable=True, deployed=True) is LifecycleState.Operational
    assert component_lifecycle_state(reachable=True, deployed=False) is LifecycleState.Standby
    assert component_lifecycle_state(reachable=False, deployed=False) is LifecycleState.Unreachable


def test_deployed_while_unreachable_is_a_fault_not_a_degree():
    """A commanded end state reported by something unreachable is a reporting defect."""
    assert component_lifecycle_state(reachable=False, deployed=True) is LifecycleState.Faulted


def test_unreported_halves_read_as_unreachable():
    """`None` is falsy, so an unmigrated component cannot claim standby by omission."""
    assert component_lifecycle_state(reachable=None, deployed=None) is LifecycleState.Unreachable


def test_aggregation_takes_the_weakest():
    assert aggregate_lifecycle_state([LifecycleState.Operational, LifecycleState.Standby]) is LifecycleState.Standby
    assert aggregate_lifecycle_state([LifecycleState.Operational, LifecycleState.Unreachable]) is LifecycleState.Unreachable
    assert aggregate_lifecycle_state([LifecycleState.Operational, LifecycleState.Operational]) is LifecycleState.Operational


def test_one_faulted_component_faults_the_aggregate():
    """Faulted is absorbing rather than ranked: it is not merely the weakest rung."""
    assert aggregate_lifecycle_state([LifecycleState.Operational, LifecycleState.Faulted]) is LifecycleState.Faulted
    assert aggregate_lifecycle_state([LifecycleState.Unreachable, LifecycleState.Faulted]) is LifecycleState.Faulted


def test_no_components_is_unreachable():
    """`all([])` would say True, which is the wrong answer for a machine with nothing reached."""
    assert aggregate_lifecycle_state([]) is LifecycleState.Unreachable


def test_the_status_models_carry_the_halves_and_default_to_unreported():
    for status in (BaseStatus(), ComponentStatus()):
        assert status.reachable is None
        assert status.deployed is None
        assert status.why_not_reachable is None
        assert status.why_not_deployed is None


def test_the_power_switch_is_pure_presence():
    """It has nothing to deploy, so it must not hold its unit below operational."""
    switch = object.__new__(DliPowerSwitch)
    switch.hostname = "mastps01"
    switch.ipaddr = "10.23.2.101"

    switch._detected = True
    assert switch.reachable is True
    assert switch.deployed is True
    assert switch.operational is True
    assert switch.why_not_operational == []

    switch._detected = False
    assert switch.reachable is False
    assert switch.operational is False
    assert switch.why_not_operational == ["power-switch: [mastps01:10.23.2.101] not detected"]


def test_the_halves_reach_the_wire_through_component_status():
    """One construction site, so a component that answers them cannot fail to publish them."""
    status = Migrated(reachable=True, deployed=False).component_status()

    assert status.reachable is True
    assert status.deployed is False
    assert status.why_not_deployed == ["migrated: not at a preset"]
    assert status.operational is False


def test_an_unmigrated_component_publishes_unreported_halves():
    status = Unmigrated(operational=True).component_status()

    assert status.reachable is None
    assert status.deployed is None
    assert status.operational is True
