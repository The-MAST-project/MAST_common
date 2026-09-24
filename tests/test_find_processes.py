"""`find_processes` and `process_session_id`: every match, optionally within one logon session."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

import common.process as process_mod
from common.process import find_processes, process_session_id


def _proc(pid: int, name: str, cmdline: list[str] | None):
    return SimpleNamespace(info={"pid": pid, "name": name, "cmdline": cmdline})


PROCS = [
    _proc(10, "PWI4.exe", [r"C:\Program Files (x86)\PlaneWave Instruments\PlaneWave Interface 4\PWI4.exe"]),
    _proc(11, "PWI4.exe", [r"C:\Program Files (x86)\PlaneWave Instruments\PlaneWave Interface 4\PWI4.exe"]),
    _proc(20, "python.exe", ["python.exe", "-m", "supervision", "--role", "supervisor"]),
    _proc(30, "System", None),  # psutil reports None for a command line it may not read
]
SESSIONS = {10: 0, 11: 1, 20: 1}  # pid 30: unreadable


@pytest.fixture(autouse=True)
def fake_processes(monkeypatch):
    monkeypatch.setattr(process_mod.psutil, "process_iter", lambda attrs: iter(PROCS))

    def session_of(pid):
        if pid not in SESSIONS:
            raise PermissionError(5, "Access is denied")
        return SESSIONS[pid]

    monkeypatch.setattr(process_mod, "process_session_id", session_of)


def _pids(procs):
    return [p.info["pid"] for p in procs]


def test_every_match_by_name_is_returned():
    assert _pids(find_processes(name="PWI4.exe")) == [10, 11]


def test_a_pattern_matches_the_command_line_and_tolerates_an_unreadable_one():
    assert _pids(find_processes(patt=r"-m\s*supervision|^supervision$")) == [20]


def test_the_session_filter_keeps_only_that_session():
    assert _pids(find_processes(name="PWI4.exe", session_id=1)) == [11]


def test_an_unreadable_session_is_left_out_of_a_filtered_result():
    assert _pids(find_processes(name="System", session_id=0)) == []


@pytest.mark.parametrize("kwargs", [{}, {"name": "a", "patt": "b"}])
def test_exactly_one_criterion_is_required(kwargs):
    with pytest.raises(ValueError):
        find_processes(**kwargs)


@pytest.mark.skipif(sys.platform == "win32", reason="the off-Windows refusal")
def test_session_ids_exist_only_on_windows(monkeypatch):
    monkeypatch.undo()  # the real process_session_id
    with pytest.raises(NotImplementedError):
        process_session_id(os.getpid())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows logon sessions")
def test_this_process_has_a_session(monkeypatch):
    monkeypatch.undo()
    assert isinstance(process_session_id(os.getpid()), int)
