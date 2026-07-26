"""Test bootstrap: make the repo importable as the ``common`` package.

Two shims, both no-ops on the platforms the code actually deploys to
(Windows units, Linux control hosts):

1. The repo root *is* the ``common`` package (root ``__init__.py``), but the
   clone directory is usually named ``MAST_common`` (or ``src/common`` inside a
   consumer), so a plain ``sys.path`` entry cannot provide ``import common``.
   Install an explicit module alias for the repo root, so the tests run in any
   clone with no environment setup.

2. ``Filer.__init__`` supports Windows and Linux only; on macOS it raises at
   import time (``common.utils`` builds a module-level ``Filer``) and its
   Linux paths (``/Storage/...``) are unwritable anyway. On Darwin only, point
   every ``Filer`` location at a per-session temp directory so the import
   chain (and ``init_log``'s file handler) works on developer Macs. Remove
   once MAST_common gains real Darwin support.
"""

from __future__ import annotations

import importlib.util
import platform
import sys
import tempfile
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


def _shim_filer_for_darwin() -> None:
    if platform.system() != "Darwin":
        return
    import common.filer as filer_module

    tmp_root = tempfile.mkdtemp(prefix="mast-common-tests-")
    location = filer_module.Location(None, tmp_root)

    def _darwin_init(self, logger=None):
        self.local = location
        self.shared = location
        self.ram = location
        self.tops = {
            filer_module.FilerTop.Local: self.local,
            filer_module.FilerTop.Shared: self.shared,
            filer_module.FilerTop.Ram: self.ram,
        }
        self.logger = logger

    filer_module.Filer.__init__ = _darwin_init


_install_common_alias()
_shim_filer_for_darwin()
