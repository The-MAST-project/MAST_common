"""A plan's status polls must yield the event loop while they wait (#87).

`wait_for_guiding` and `wait_for_spec_done` run on MAST-control's event loop. A blocking
sleep between polls freezes every other task on that loop for the whole interval.
"""

from __future__ import annotations

import asyncio

import pytest

from common import activities
from common.activities import Activities, PlanActivities
from common.models import plans
from common.models.plans import Plan
from common.models.statuses import ComponentStatus
from common.models.targets import Target

POLL_SECONDS = 0.01


class SilentNotifier:
    def ui_notification(self, *args, **kwargs):
        pass


@pytest.fixture
def plan(monkeypatch) -> Plan:
    monkeypatch.setattr(plans, "STATUS_POLL_SECONDS", POLL_SECONDS)
    monkeypatch.setattr(activities, "Notifier", SilentNotifier)
    p = Plan(target=Target(ra_hours=1.0, dec_degrees=10.0))
    Activities.__init__(p)
    p.committed_unit_apis = []
    return p


def run_beside_a_ticker(waiter) -> list[str]:
    """Run `waiter` with a concurrent task; return the order in which they made progress."""
    events: list[str] = []

    async def ticker():
        events.append("tick")

    async def main():
        await asyncio.gather(waiter(events), ticker())

    asyncio.run(main())
    return events


def test_wait_for_guiding_yields_between_polls(plan, monkeypatch):
    async def waiter(events):
        async def fetch_statuses(units, spec=None):
            events.append("poll")
            return []

        monkeypatch.setattr(plan, "fetch_statuses", fetch_statuses)
        await plan.wait_for_guiding()

    assert run_beside_a_ticker(waiter) == ["tick", "poll"]
    assert not plan.is_active(PlanActivities.WaitingForGuiding)


def test_wait_for_spec_done_yields_between_polls(plan, monkeypatch):
    idle = ComponentStatus(operational=True, activities=Activities.Idle)

    async def waiter(events):
        async def get_spec_status():
            events.append("poll")
            return idle

        monkeypatch.setattr(plan, "get_spec_status", get_spec_status)
        monkeypatch.setattr(plans.json, "dumps", lambda *a, **k: "")
        await plan.wait_for_spec_done()

    assert run_beside_a_ticker(waiter) == ["tick", "poll"]
