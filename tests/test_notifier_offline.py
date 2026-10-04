"""A UI notification must not be able to fail the operation it announces (#133).

`start_activity` raises its flag and then notifies. The notification path reaches the
control machine by name, so when that name does not resolve, nothing about it may surface
on the caller's thread: the flag is raised, the call returns, and the notification waits in
the queue until the control machine can be reached again.
"""

from __future__ import annotations

import socket
import threading
import time
from enum import IntFlag, auto

import pytest

import common.api as api
import common.notifications as notifications
from common.activities import Activities
from common.canonical import CanonicalResponse
from common.notifications import NotificationInitiator, Notifier

SEND_DEADLINE = 5.0


class UnitActivities(IntFlag):
    Idle = 0
    Exposing = auto()


class Component(Activities):
    pass


class FlakyNotificationApi:
    """Stands in for `NotificationApi`: construction fails until `reachable` is set."""

    reachable = threading.Event()
    sent: list[str] = []
    constructed_on: list[threading.Thread] = []

    def __init__(self, site_name=None):
        FlakyNotificationApi.constructed_on.append(threading.current_thread())
        if not FlakyNotificationApi.reachable.is_set():
            raise socket.gaierror(11001, "getaddrinfo failed")

    async def put(self, method, data=None):
        FlakyNotificationApi.sent.append(data)
        return CanonicalResponse(value="ok")


@pytest.fixture
def notifier(monkeypatch):
    FlakyNotificationApi.reachable.clear()
    FlakyNotificationApi.sent.clear()
    FlakyNotificationApi.constructed_on.clear()
    monkeypatch.setattr(api, "NotificationApi", FlakyNotificationApi)
    monkeypatch.setattr(
        notifications,
        "_build_initiator",
        lambda: NotificationInitiator(site="ns", type="unit", hostname="mast99", project="mast"),
    )
    monkeypatch.setattr(Notifier, "_instance", None)
    yield
    instance = Notifier._instance
    if instance is not None and getattr(instance, "stop_event", None) is not None:
        instance.stop_event.set()
        instance.notification_event.set()


def wait_for(predicate) -> bool:
    deadline = time.monotonic() + SEND_DEADLINE
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_notifier_constructs_when_the_control_machine_does_not_resolve(notifier):
    Notifier()


def test_start_activity_raises_its_flag_and_returns(notifier):
    component = Component()

    component.start_activity(UnitActivities.Exposing)

    assert component.is_active(UnitActivities.Exposing)


def test_end_activity_clears_its_flag_and_returns(notifier):
    component = Component()
    component.start_activity(UnitActivities.Exposing)

    component.end_activity(UnitActivities.Exposing)

    assert not component.is_active(UnitActivities.Exposing)


def test_the_caller_never_builds_the_api(notifier):
    component = Component()

    component.start_activity(UnitActivities.Exposing)
    assert wait_for(lambda: FlakyNotificationApi.constructed_on)

    assert threading.current_thread() not in FlakyNotificationApi.constructed_on


def test_a_queued_notification_is_sent_once_the_control_machine_is_reachable(notifier):
    component = Component()
    component.start_activity(UnitActivities.Exposing)
    assert wait_for(lambda: FlakyNotificationApi.constructed_on)
    assert FlakyNotificationApi.sent == []

    FlakyNotificationApi.reachable.set()

    assert wait_for(lambda: FlakyNotificationApi.sent)
    assert "Exposing" in FlakyNotificationApi.sent[0]


def test_an_outage_logs_its_start_and_end_only(notifier, monkeypatch):
    logged: list[tuple[str, str]] = []

    class Recorder:
        def warning(self, msg):
            logged.append(("warning", msg))

        def info(self, msg):
            logged.append(("info", msg))

        def debug(self, msg):
            pass

    monkeypatch.setattr(notifications, "logger", Recorder())
    component = Component()
    for _ in range(3):
        component.start_activity(UnitActivities.Exposing)
        component.end_activity(UnitActivities.Exposing)
    assert wait_for(lambda: len(FlakyNotificationApi.constructed_on) >= 2)

    FlakyNotificationApi.reachable.set()
    assert wait_for(lambda: len(FlakyNotificationApi.sent) >= 1)
    assert wait_for(lambda: any(level == "info" for level, _ in logged))

    assert [level for level, _ in logged] == ["warning", "info"]
