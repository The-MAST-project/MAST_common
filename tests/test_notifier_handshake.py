"""Assignment notifications are a handshake; UI notifications are ephemeral.

An assignment notification leaves its queue only when the controller answers `ok` -- it
answers so once it has acted on it -- and is sent again otherwise, backing off. It is never
evicted by UI notifications, goes before them, and is given up after a maximum age. A UI
notification is replaced by a newer one for the same elements. `BaseApi.put` returns its
failures rather than raising them, and a send that came back with errors used to count as sent.
"""

from __future__ import annotations

import threading
import time

import pytest

import common.api as api
import common.notifications as notifications
from common.canonical import CanonicalResponse
from common.models.assignments import AssignmentNotification
from common.notifications import NotificationInitiator, Notifier, UiUpdateSpec

DEADLINE = 5.0


class ScriptedApi:
    """Answers each send with the next scripted response; the last one repeats."""

    answers: list = []
    sent: list[str] = []
    timeouts: list[float] = []
    lock = threading.Lock()

    def __init__(self, site_name=None):
        pass

    async def put(self, method, data=None, timeout=None):
        with ScriptedApi.lock:
            ScriptedApi.sent.append(data)
            ScriptedApi.timeouts.append(timeout)
            answer = ScriptedApi.answers.pop(0) if len(ScriptedApi.answers) > 1 else ScriptedApi.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


OK = CanonicalResponse(value="ok")
REFUSED = CanonicalResponse(errors=["timeout after 10 seconds"])


@pytest.fixture
def notifier(monkeypatch):
    ScriptedApi.answers = [OK]
    ScriptedApi.sent = []
    ScriptedApi.timeouts = []
    monkeypatch.setattr(api, "NotificationApi", ScriptedApi)
    monkeypatch.setattr(
        notifications,
        "_build_initiator",
        lambda: NotificationInitiator(site="ns", type="spec", hostname="mast-ns-spec", project="mast"),
    )
    monkeypatch.setattr(Notifier, "_instance", None)
    monkeypatch.setattr(Notifier, "BACKOFF_FIRST", 0.01)
    monkeypatch.setattr(Notifier, "BACKOFF_MAX", 0.05)
    yield
    instance = Notifier._instance
    if instance is not None:
        instance.stop_event.set()
        instance.notification_event.set()


def wait_for(predicate) -> bool:
    deadline = time.monotonic() + DEADLINE
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def assignment(state="completed") -> AssignmentNotification:
    return AssignmentNotification(
        assignment_id="01JAD8Q4ZK7V3M2XG9T6R5N0HB",
        state=state,
        initiator=NotificationInitiator(site="ns", type="spec", hostname="mast-ns-spec"),
    )


def sent_states() -> list[str]:
    return [s for s in ScriptedApi.sent if "assignment_notification" in s]


class TestTheHandshake:
    def test_an_error_is_not_delivery_and_it_is_sent_again(self, notifier):
        ScriptedApi.answers = [REFUSED, REFUSED, OK]
        Notifier().assignment_notification(assignment())

        assert wait_for(lambda: len(ScriptedApi.sent) == 3)
        assert wait_for(lambda: not Notifier().assignment_queue)
        time.sleep(0.1)
        assert len(ScriptedApi.sent) == 3, "acknowledged: not sent again"

    def test_an_answer_other_than_ok_is_not_an_acknowledgement(self, notifier):
        ScriptedApi.answers = [CanonicalResponse(value="maybe"), OK]
        Notifier().assignment_notification(assignment())
        assert wait_for(lambda: len(ScriptedApi.sent) == 2 and not Notifier().assignment_queue)

    def test_an_exception_is_not_delivery_either(self, notifier):
        ScriptedApi.answers = [OSError("unreachable"), OK]
        Notifier().assignment_notification(assignment())
        assert wait_for(lambda: len(ScriptedApi.sent) == 2 and not Notifier().assignment_queue)

    def test_it_is_given_longer_than_a_ui_notification(self, notifier):
        Notifier().assignment_notification(assignment())
        assert wait_for(lambda: ScriptedApi.timeouts)
        assert ScriptedApi.timeouts[0] == Notifier.ASSIGNMENT_TIMEOUT

    def test_in_order_one_at_a_time(self, notifier):
        ScriptedApi.answers = [REFUSED, OK]
        notifier_ = Notifier()
        notifier_.assignment_notification(assignment("in-progress"))
        notifier_.assignment_notification(assignment("completed"))
        assert wait_for(lambda: not notifier_.assignment_queue)
        states = ['"in-progress"' in s for s in sent_states()]
        assert states == [True, True, False], "the first is retried before the second is tried"

    def test_given_up_after_its_maximum_age(self, notifier, monkeypatch):
        monkeypatch.setattr(Notifier, "ASSIGNMENT_MAX_AGE", 0.1)
        ScriptedApi.answers = [REFUSED]
        Notifier().assignment_notification(assignment())
        assert wait_for(lambda: not Notifier().assignment_queue)


class TestTheTwoQueues:
    def test_ui_updates_never_evict_an_assignment(self, notifier):
        ScriptedApi.answers = [OSError("unreachable")]
        n = Notifier()
        n.assignment_notification(assignment())
        for i in range(Notifier.NOTIFICATION_QUEUE_SIZE * 3):
            n.ui_notification(UiUpdateSpec(path=["x", str(i)], value=i))
        assert len(n.assignment_queue) == 1
        assert len(n.notification_queue) == Notifier.NOTIFICATION_QUEUE_SIZE

    def test_an_assignment_goes_before_waiting_ui_updates(self, notifier):
        ScriptedApi.answers = [OSError("unreachable")]
        n = Notifier()
        n.ui_notification(UiUpdateSpec(path=["x"], value=1))
        n.assignment_notification(assignment())
        assert wait_for(lambda: ScriptedApi.sent)
        ScriptedApi.answers = [OK]
        assert wait_for(lambda: not n.assignment_queue and not n.notification_queue)
        delivered_after_outage = [s for s in ScriptedApi.sent[-2:]]
        assert "assignment_notification" in delivered_after_outage[0]

    def test_a_newer_ui_update_replaces_one_for_the_same_element(self, notifier):
        ScriptedApi.answers = [OSError("unreachable")]
        n = Notifier()
        n.ui_notification(UiUpdateSpec(path=["stage", "position"], value=1))
        n.ui_notification(UiUpdateSpec(path=["stage", "position"], value=2))
        n.ui_notification(UiUpdateSpec(path=["focuser", "position"], value=3))
        waiting = [p.data for p in n.notification_queue]
        assert len(waiting) == 2
        assert '"value":2' in waiting[0].replace(" ", "") and '"value":3' in waiting[1].replace(" ", "")
