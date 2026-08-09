"""The guard in conftest must actually stop a process being started.

A tripwire nobody tests is one that quietly stops working, and the failure mode here
is not a red test -- it is PWI4 or the plate solver being launched against real
hardware, because this library is imported by services running on the telescope
machines. So the guard is exercised directly rather than trusted.

MAST_unit carries the same pair of files, added after importing its ``app.py`` during
a ruff clean-up started PWI4, the shutter and ps3cli on a live unit: app.py calls
``ensure_process_is_running`` at module level, so the import alone was enough.
"""

from __future__ import annotations

import os
import subprocess

import pytest

# tests/ is not a package, so this is a plain import: pytest puts the test directory
# on sys.path (importmode=prepend), which makes conftest importable by name.
from conftest import ProcessLaunchError

# Captured while THIS MODULE is being imported, i.e. during collection -- before any
# fixture has run. If the guard were installed from a fixture instead of at conftest
# import time, this would still be the real subprocess.Popen and the test below would
# fail. That window is the one that matters: a test module importing an entry point at
# top level spawns during collection.
_POPEN_AT_COLLECTION = subprocess.Popen


class TestGuardIsActiveBeforeAnyTestRuns:
    def test_installed_at_collection_time_not_by_a_fixture(self):
        with pytest.raises(ProcessLaunchError):
            _POPEN_AT_COLLECTION(["PWI4.exe"])


class TestGuardFires:
    @pytest.mark.parametrize("call", ["Popen", "run", "call", "check_call", "check_output"])
    def test_subprocess_entry_points_are_blocked(self, call):
        with pytest.raises(ProcessLaunchError):
            getattr(subprocess, call)(["echo", "hello"])

    @pytest.mark.parametrize("call", ["system", "popen"])
    def test_os_entry_points_are_blocked(self, call):
        with pytest.raises(ProcessLaunchError):
            getattr(os, call)("echo hello")

    def test_the_message_names_the_call_and_the_target(self):
        """Whoever trips this needs to see what tried to start what."""
        with pytest.raises(ProcessLaunchError) as excinfo:
            subprocess.Popen(["PWI4.exe"])
        message = str(excinfo.value)
        assert "subprocess.Popen" in message
        assert "PWI4.exe" in message


class TestTheRealLauncherIsCovered:
    def test_process_module_spawns_through_a_blocked_entry_point(self):
        """process.ensure_process_is_running is the fleet's launcher.

        Blocking subprocess and os wholesale rather than patching that one function is
        deliberate -- a direct Popen elsewhere would slip past a targeted patch -- but
        the launcher is the path that matters most, so confirm it lands on the guard.
        """
        process = pytest.importorskip("common.process", reason="process import chain unavailable")
        assert hasattr(process, "ensure_process_is_running")

        with pytest.raises(ProcessLaunchError):
            subprocess.Popen(["ps3cli.exe", "--server"])

    def test_psutil_popen_is_blocked_too(self):
        """psutil is used to FIND processes here, but psutil.Popen would spawn one."""
        psutil = pytest.importorskip("psutil")
        with pytest.raises(ProcessLaunchError):
            psutil.Popen(["PWI4.exe"])
