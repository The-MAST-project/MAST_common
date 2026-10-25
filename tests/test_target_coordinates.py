"""`target_coordinates`: coordinates first, then the name, never a silent fall-through
(plans/control_acquisition_design.md 3.8). The resolver is stubbed: no network in tests."""

from __future__ import annotations

import pytest

import common.object_resolver as resolver_module
from common.models.assignments import AssignmentNotification
from common.models.plans import PlanStage
from common.notifications import NotificationInitiator
from common.object_resolver import ObjectNameError, ResolvedObject
from common.parsers import target_coordinates

#: Explicit, so a notification needs no bootstrap config to build its own.
SENDER = NotificationInitiator(site="ns", type="unit", hostname="mast01", project="mast")

M101 = ResolvedObject(name="M101", ra_j2000_hours=14.0535, dec_j2000_degs=54.34875, resolver="sesame")


@pytest.fixture
def asked(monkeypatch) -> list[tuple[str, float]]:
    calls: list[tuple[str, float]] = []

    def resolve(name, total_timeout):
        calls.append((name, total_timeout))
        if name == "M101":
            return M101
        raise ObjectNameError(f"'{name}' did not resolve")

    monkeypatch.setattr(resolver_module, "resolve_object_name", resolve)
    return calls


class TestCoordinatesWin:
    @pytest.mark.parametrize(("ra", "dec"), [("12:30:45", "+20:15:00"), (12.5125, 20.25), ("12 30 45", "20 15 00")])
    def test_both_valid_are_used_and_the_name_is_not_resolved(self, asked, ra, dec):
        assert target_coordinates(ra, dec, "M101") == (pytest.approx(12.5125), pytest.approx(20.25), None)
        assert asked == []

    def test_zero_is_a_coordinate(self, asked):
        assert target_coordinates(0.0, 0.0, "M101") == (0.0, 0.0, None)
        assert asked == []


class TestTheNameWhenTheCoordinatesCannot:
    @pytest.mark.parametrize(
        ("ra", "dec"),
        [(None, None), ("", ""), ("12:30:45", None), (None, "+20:15:00"), ("25:00:00", "+20:15:00"), ("x", "y")],
    )
    def test_missing_or_unusable_coordinates_resolve_the_name(self, asked, ra, dec):
        assert target_coordinates(ra, dec, "M101") == (14.0535, 54.34875, M101)
        assert [name for name, _ in asked] == ["M101"]

    def test_a_name_that_does_not_resolve_raises(self, asked):
        with pytest.raises(ObjectNameError):
            target_coordinates(None, None, "no such thing")

    def test_the_timeout_is_passed_on(self, asked):
        target_coordinates(None, None, "M101", resolve_timeout=3.0)
        assert asked == [("M101", 3.0)]


class TestNeither:
    @pytest.mark.parametrize("name", [None, ""])
    def test_nothing_given_is_none_for_the_caller_to_decide(self, asked, name):
        assert target_coordinates(None, None, name) == (None, None, None)
        assert asked == []

    @pytest.mark.parametrize(("ra", "dec"), [("25:00:00", "+20:15:00"), ("12:30:45", None), (None, "+91:00:00")])
    def test_coordinates_given_but_unusable_say_so(self, asked, ra, dec):
        with pytest.raises(ValueError, match="supplied but unusable"):
            target_coordinates(ra, dec, None)
        assert asked == []


class TestTheNotificationsStage:
    def test_a_notification_carries_its_stage(self):
        n = AssignmentNotification(assignment_id="01J", state="in-progress", initiator=SENDER, stage=PlanStage.Acquisition)
        assert n.stage is PlanStage.Acquisition
        assert AssignmentNotification.model_validate_json(n.model_dump_json()).stage is PlanStage.Acquisition

    def test_one_without_a_stage_still_parses(self):
        n = AssignmentNotification(assignment_id="01J", state="in-progress", initiator=SENDER, shared_subpath="highspec")
        assert n.stage is None
