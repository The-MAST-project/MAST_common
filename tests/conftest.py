"""Test bootstrap: make the repo importable as the ``common`` package.

Two shims:

1. The repo root *is* the ``common`` package (root ``__init__.py``), but the
   clone directory is usually named ``MAST_common`` (or ``src/common`` inside a
   consumer), so a plain ``sys.path`` entry cannot provide ``import common``.
   Install an explicit module alias for the repo root, so the tests run in any
   clone with no environment setup.

2. Every ``Filer`` points at temporary folders, one per role, **on every platform**
   (``common.testing.filer``). This used to be done on macOS only, and with one folder
   for all three roles: on the Windows runner a bare ``Filer()`` was ``C:/MAST/`` for
   both ``ram`` and ``shared`` -- the layout that deletes products by moving them onto
   themselves -- and on a unit or the control host the suite reached the real RAM disk
   and share. Tests of the real layout build it through ``real_filer_init`` /
   ``real_filer`` (plans/filing-areas-design.md, 13).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _install_common_alias() -> None:
    if "common" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "common",
        _REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(_REPO_ROOT)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["common"] = module
    spec.loader.exec_module(module)


_install_common_alias()

from common.testing import filer as _filer_testing  # noqa: E402 -- needs the alias above

_filer_testing.isolate_filer(prefix="mast-common-tests-")


# Programs a test may legitimately start. Keep this list short and justified: it is the
# one hole in the guard below.
#
# fontconfig -- matplotlib's font manager shells out to `fc-list` while importing, on
# Linux only, so this surfaces on the ubuntu half of the CI matrix and never on Windows,
# where matplotlib finds fonts differently. Enumerating fonts is not driving hardware.
#
# openssl -- the stdlib cannot verify a certificate chain without a live handshake, and
# checking that the pinned CA signs the control machine's certificate is not driving
# hardware either.
ALLOWED = {"fc-list", "fc-match", "openssl"}


class ProcessLaunchError(RuntimeError):
    """Raised when a test tries to start an external process."""


def _block_external_processes() -> None:
    """Fail loudly if anything under test starts an external process.

    Installed at conftest IMPORT time, not from a fixture. Fixtures -- even autouse
    session ones -- first run when the first test runs, which is after collection, and
    collection imports every test module. A test module that imports an entry point at
    top level would therefore spawn before any fixture could stop it. conftest is
    imported before collection begins, so patching here covers that window too.

    Nothing is started and then killed: the replacements raise instead of spawning, so
    no process is ever created.

    This library is imported by services running on the telescope machines, and
    ``common.process.ensure_process_is_running`` is the fleet's process launcher -- so
    a test that reaches it does not pollute a sandbox, it starts PWI4 or the plate
    solver against real hardware. MAST_unit carries the same guard, added after
    importing its ``app.py`` during a clean-up did exactly that: app.py calls the
    launcher at module level, so the import alone was enough.

    The low-level entry points are blocked wholesale rather than the launcher being
    patched -- a direct ``subprocess.Popen`` elsewhere would slip past a targeted
    patch. If a test ever needs a real subprocess, allow it explicitly here rather
    than removing the guard.

    On a CI runner the guarded programs are not installed, so a spawn would fail
    there anyway -- just slowly and with a confusing error. The case this exists for
    is the fleet's own machines, where the suite is run and the programs are real.
    """
    denied = {
        subprocess: ("Popen", "run", "call", "check_call", "check_output"),
        os: ("system", "popen", "startfile", "execv", "execvp", "spawnl", "spawnv"),
    }
    # process.py spawns through subprocess.Popen and uses psutil only to FIND
    # processes -- but psutil.Popen exists, so close that door too.
    try:
        import psutil

        denied[psutil] = ("Popen",)
    except ImportError:
        pass

    def executable_of(target) -> str:
        """Best-effort program name, whether the caller passed a list or a string."""
        if isinstance(target, (list, tuple)) and target:
            target = target[0]
        return os.path.basename(str(target)).lower()

    def deny(name, original):
        def _deny(*args, **kwargs):
            target = args[0] if args else kwargs.get("args") or kwargs.get("cmd") or "?"
            if executable_of(target) in ALLOWED:
                return original(*args, **kwargs)
            raise ProcessLaunchError(
                f"the test suite tried to start a process via {name}: {target!r}. "
                "On a MAST machine this would drive real hardware. If a test genuinely "
                "needs this, allow it explicitly in tests/conftest.py."
            )

        return _deny

    for module, names in denied.items():
        for name in names:
            original = getattr(module, name, None)
            if original is None:  # os.startfile is Windows-only, etc.
                continue
            setattr(module, name, deny(f"{module.__name__}.{name}", original))


_block_external_processes()


# Tests stay off the internet: a connection off this machine fails the test, unless it is
# marked `network` -- and those are skipped in CI and when the IERS host is unreachable.
# Installed at import, like the process guard, so collection is covered too. See
# common/testing/network.py; MAST_unit's conftest wires the same module.
from common.testing import network as _network  # noqa: E402 -- needs the alias installed above

_network.install()
pytest_configure = _network.pytest_configure
pytest_collection_modifyitems = _network.pytest_collection_modifyitems
_allow_network_for_marked_tests = _network.allow_network_for_marked_tests
