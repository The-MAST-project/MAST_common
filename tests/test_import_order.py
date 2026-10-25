"""Every `common` entry point imports on its own, with no other `common` module loaded.

`common.utils` builds a `Filer` at import, and `Filer()` reads the bootstrap config through
the `common.config` package -- so `common.config` must never import from `common.utils`,
which is half-initialised at that moment. It did (`function_name`), and the unit app died
at `import common.activities` while this suite passed: by the time any test ran, collection
had already loaded `common.config` first.

So each case sets every loaded `common` module aside, imports the entry point from scratch,
and puts the originals back. In-process rather than a fresh interpreter: conftest blocks
subprocesses, and an import cycle needs nothing more than an empty `sys.modules` to show.
"""

from __future__ import annotations

import importlib
import sys

import pytest

ENTRY_POINTS = [
    "common.activities",  # MAST_unit's app.py imports this first
    "common.utils",
    "common.filer",
    "common.config",
    "common.mast_logging",
    "common.paths",
    "common.api",
]


def _common_modules() -> list[str]:
    return [name for name in sys.modules if name == "common" or name.startswith("common.")]


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_imports_first(module: str):
    saved = {name: sys.modules.pop(name) for name in _common_modules()}
    try:
        importlib.import_module(module)
    finally:
        for name in _common_modules():
            del sys.modules[name]
        sys.modules.update(saved)


def test_function_name_is_still_exported_by_utils():
    from common.introspection import function_name
    from common.utils import function_name as from_utils

    assert from_utils is function_name
