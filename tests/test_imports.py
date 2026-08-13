"""Every module the rest of the suite guards with ``importorskip`` must import here.

Those guards exist so a developer missing a dependency gets skips instead of a wall of
collection errors. On CI the same behaviour is a trap: with nothing installed, 190 of the
201 tests skip and the run reports **green** having verified almost nothing.

So this module imports the same things without a guard. It is the one place the suite is
allowed to fail loudly on a broken environment, and it must stay ahead of the guards --
if a new test module starts with ``pytest.importorskip("common.x")``, add ``common.x``
here too.

Platform note: the MAST production platforms are Windows (unit, spec) and Linux (control,
gui), and this repo runs on both, so everything below must import on both. ``common.ascom``
is excluded deliberately -- it imports ``pywintypes`` and ``win32com.client`` unguarded and
is Windows-only by construction; nothing under tests/ uses it.
"""

from __future__ import annotations

import importlib
import platform

import pytest

# Kept in step with the importorskip() calls elsewhere under tests/.
GUARDED_BY_OTHER_TESTS = [
    "common.config.local",
    "common.config.phd2",
    "common.filer",
    "common.models.statuses",
    "common.models.targets",
    "common.parsers",
]

# Imported indirectly by the above, or central enough that a failure here localises the
# fault far better than a downstream collection error would.
CORE = [
    "common.deep",
    "common.mast_logging",
    "common.paths",
    "common.utils",
]

WINDOWS_ONLY = ["common.ascom"]


@pytest.mark.parametrize("module", GUARDED_BY_OTHER_TESTS)
def test_module_guarded_elsewhere_actually_imports(module):
    """If this fails, the suite is about to skip most of itself and call that success."""
    importlib.import_module(module)


@pytest.mark.parametrize("module", CORE)
def test_core_module_imports(module):
    importlib.import_module(module)


@pytest.mark.skipif(platform.system() != "Windows", reason="pywin32 is Windows-only by construction")
@pytest.mark.parametrize("module", WINDOWS_ONLY)
def test_windows_only_module_imports(module):
    importlib.import_module(module)
