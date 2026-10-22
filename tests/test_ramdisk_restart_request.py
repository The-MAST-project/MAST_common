"""The RAM-disk restart request: asked only when D: is gone, acted on only when restored.

plans/filing-areas-design.md 8.1. Restarting wipes D:, so the request is sent only for a
RAM disk that is GONE -- never a full one, whose unmoved products a restart would destroy,
and never for a failure about one path. The supervisor is a scripted fake; `SupervisorApi`'s
mapping of HTTP statuses is tested through httpx's MockTransport, with no socket.
"""

from __future__ import annotations

import errno
import logging
import threading

import httpx
import pytest

import common.api as api_module
import common.filer as filer_module
from common.api import RamdiskRestartAnswer, SupervisorApi
from common.filer import RAMDISK_RESTART_REQUESTER, Filer, FilingArea, Location
from common.testing.filer import reset_filing_areas


class FakeSupervisor:
    def __init__(self, answers, on_call=None):
        self.answers = list(answers)
        self.calls = 0
        self.on_call = on_call
        self.requested_by: list[str] = []

    def restart_ramdisk(self, requested_by):
        self.calls += 1
        self.requested_by.append(requested_by)
        if self.on_call:
            self.on_call(self.calls)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return answer, f"fake answer {self.calls}"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(filer_module, "_probe_cache", {})
    monkeypatch.setattr(filer_module, "_probe_threads", {})
    monkeypatch.setattr(Filer, "MONITOR_INTERVAL_SECONDS", 30.0)  # the request, not the monitor, promotes here
    monkeypatch.setattr(Filer, "RAMDISK_RESTART_POLL_SECONDS", 0.01)
    reset_filing_areas()
    Filer._ramdisk_restart_thread = None
    yield
    _wait_for_request()
    reset_filing_areas()
    monkeypatch.setattr(Filer, "_supervisor_factory", None)


def _wait_for_request(timeout: float = 5.0) -> None:
    thread = Filer._ramdisk_restart_thread
    if thread is not None:
        thread.join(timeout)
        assert not thread.is_alive(), "the restart request should have finished"


@pytest.fixture
def unit(tmp_path):
    for name in ("ram", "local/ram-fallback", "local/share-fallback", "share/mast00"):
        (tmp_path / name).mkdir(parents=True)
    filer = object.__new__(Filer)
    filer.ram = Location(str(tmp_path / "ram"))
    filer.local = Location(str(tmp_path / "local"))
    filer.ram_fallback = Location(str(tmp_path / "local/ram-fallback"))
    filer.share_fallback = Location(str(tmp_path / "local/share-fallback"))
    filer.share_root = Location(str(tmp_path / "share"))
    filer.shared = Location(str(tmp_path / "share/mast00"))
    filer.logger = logging.getLogger("test.ramdisk-restart")
    filer.ram_dir = tmp_path / "ram"  # type: ignore[attr-defined]
    return filer


def _use(monkeypatch, supervisor):
    monkeypatch.setattr(Filer, "_supervisor_factory", lambda: supervisor)


def _ram_gone(unit):
    unit.ram_dir.rmdir()
    return unit.report_failure(FilingArea.RAM, unit.ram.root + "x.fits", FileNotFoundError(errno.ENOENT, "x"))


def _revive(unit):
    return lambda calls: unit.ram_dir.mkdir(exist_ok=True)


class TestWhenItIsAsked:
    @pytest.mark.parametrize("answer", [RamdiskRestartAnswer.RESTORED, RamdiskRestartAnswer.HEALTHY])
    def test_gone_asks_and_a_revived_disk_is_promoted(self, unit, monkeypatch, answer):
        supervisor = FakeSupervisor([answer], on_call=_revive(unit))
        _use(monkeypatch, supervisor)

        assert _ram_gone(unit) == "gone"
        _wait_for_request()

        assert supervisor.calls == 1
        assert not unit.is_demoted(FilingArea.RAM)

    def test_full_never_asks(self, unit, monkeypatch):
        supervisor = FakeSupervisor([RamdiskRestartAnswer.RESTORED])
        _use(monkeypatch, supervisor)

        assert unit.report_failure(FilingArea.RAM, "x", OSError(errno.ENOSPC, "full")) == "full"
        _wait_for_request()

        assert supervisor.calls == 0
        assert unit.is_demoted(FilingArea.RAM), "full stays on the fallback until the monitor promotes it"

    def test_a_path_level_failure_never_asks(self, unit, monkeypatch):
        supervisor = FakeSupervisor([RamdiskRestartAnswer.RESTORED])
        _use(monkeypatch, supervisor)

        assert unit.report_failure(FilingArea.RAM, unit.ram.root + "x", OSError(errno.EACCES, "denied")) is None
        assert supervisor.calls == 0

    def test_the_share_never_asks(self, unit, monkeypatch):
        supervisor = FakeSupervisor([RamdiskRestartAnswer.RESTORED])
        _use(monkeypatch, supervisor)
        exc = OSError(None, "net")
        exc.winerror = 53  # type: ignore[attr-defined]

        assert unit.report_failure(FilingArea.SHARE, "x", exc) == "unreachable"
        assert supervisor.calls == 0


class TestTheAnswers:
    def test_in_progress_is_asked_again_until_restored(self, unit, monkeypatch):
        supervisor = FakeSupervisor(
            [RamdiskRestartAnswer.IN_PROGRESS, RamdiskRestartAnswer.IN_PROGRESS, RamdiskRestartAnswer.RESTORED],
            on_call=lambda calls: unit.ram_dir.mkdir(exist_ok=True) if calls == 3 else None,
        )
        _use(monkeypatch, supervisor)

        _ram_gone(unit)
        _wait_for_request()

        assert supervisor.calls == 3
        assert not unit.is_demoted(FilingArea.RAM)

    @pytest.mark.parametrize("answer", [RamdiskRestartAnswer.GIVING_UP, RamdiskRestartAnswer.NO_ANSWER])
    def test_giving_up_or_no_supervisor_stays_on_the_fallback(self, unit, monkeypatch, answer, caplog):
        caplog.set_level(logging.WARNING)
        supervisor = FakeSupervisor([answer])
        _use(monkeypatch, supervisor)

        _ram_gone(unit)
        _wait_for_request()

        assert supervisor.calls == 1
        assert unit.is_demoted(FilingArea.RAM)
        assert any("RAM" in r.getMessage() and r.levelno >= logging.WARNING for r in caplog.records)

    def test_one_failed_revival_is_a_warning_not_giving_up(self, unit, monkeypatch, caplog):
        caplog.set_level(logging.WARNING)
        supervisor = FakeSupervisor([RamdiskRestartAnswer.FAILED])
        _use(monkeypatch, supervisor)

        _ram_gone(unit)
        _wait_for_request()

        assert supervisor.calls == 1
        assert unit.is_demoted(FilingArea.RAM)
        ram = [r for r in caplog.records if "RAM" in r.getMessage() and r.levelno >= logging.WARNING]
        assert ram and all(r.levelno == logging.WARNING for r in ram)
        assert not any("given up" in r.getMessage() for r in ram)

    def test_the_filer_says_it_is_the_one_asking(self, unit, monkeypatch):
        supervisor = FakeSupervisor([RamdiskRestartAnswer.RESTORED])
        _use(monkeypatch, supervisor)

        _ram_gone(unit)
        _wait_for_request()

        assert supervisor.requested_by == [RAMDISK_RESTART_REQUESTER]

    def test_a_restart_that_never_finishes_is_left_after_the_deadline(self, unit, monkeypatch):
        monkeypatch.setattr(Filer, "RAMDISK_RESTART_DEADLINE_SECONDS", 0.05)
        supervisor = FakeSupervisor([RamdiskRestartAnswer.IN_PROGRESS])
        _use(monkeypatch, supervisor)

        _ram_gone(unit)
        _wait_for_request()

        assert supervisor.calls >= 2
        assert unit.is_demoted(FilingArea.RAM)

    def test_a_client_that_raises_is_no_answer(self, unit, monkeypatch):
        class Broken:
            def restart_ramdisk(self, requested_by):
                raise RuntimeError("bug")

        _use(monkeypatch, Broken())

        _ram_gone(unit)
        _wait_for_request()

        assert unit.is_demoted(FilingArea.RAM)


class TestOneAtATime:
    def test_a_second_gone_while_asking_does_not_ask_again(self, unit, monkeypatch):
        release = threading.Event()
        supervisor = FakeSupervisor([RamdiskRestartAnswer.RESTORED], on_call=lambda calls: release.wait(5))
        _use(monkeypatch, supervisor)

        _ram_gone(unit)
        with Filer._state_lock:
            Filer._demoted.clear()  # a second, separate "gone" while the first request is out
        unit.report_failure(FilingArea.RAM, "y", FileNotFoundError(errno.ENOENT, "y"))
        release.set()
        _wait_for_request()

        assert supervisor.calls == 1


class TestSupervisorApi:
    @pytest.mark.parametrize(
        "status, answer",
        [
            (200, RamdiskRestartAnswer.RESTORED),
            (202, RamdiskRestartAnswer.IN_PROGRESS),
            (409, RamdiskRestartAnswer.HEALTHY),
            (502, RamdiskRestartAnswer.FAILED),
            (503, RamdiskRestartAnswer.GIVING_UP),
            (500, RamdiskRestartAnswer.NO_ANSWER),
        ],
    )
    def test_the_status_is_the_answer(self, monkeypatch, status, answer):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status, text="detail")

        real_client = httpx.Client
        monkeypatch.setattr(
            api_module.httpx,
            "Client",
            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        )

        got, _ = SupervisorApi().restart_ramdisk("filer")

        assert got is answer
        assert seen[0].method == "PUT"
        assert str(seen[0].url) == "http://127.0.0.1:8005/mast/api/v1/supervisor/ramdisk/restart?requested_by=filer"

    def test_nothing_listening_is_no_answer(self, monkeypatch):
        def refuse(request):
            raise httpx.ConnectError("refused", request=request)

        real_client = httpx.Client
        monkeypatch.setattr(
            api_module.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(refuse), **kw)
        )

        assert SupervisorApi().restart_ramdisk("filer")[0] is RamdiskRestartAnswer.NO_ANSWER
