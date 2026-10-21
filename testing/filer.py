"""Keep test suites off the real storage roots: every ``Filer`` points at temporary folders.

``common.utils`` builds a module-level ``Filer`` at import, so every module that imports it
-- nearly all of them -- would otherwise reach for the machine's real roots:

- on **Windows** a bare ``Filer()`` is ``D:/MAST/`` and ``Z:/MAST/<host>/`` when they are
  mapped, which on a unit means the suite writes beside a running unit's products; and both
  ``C:/MAST/`` when they are not, as on the CI runner, which is the layout that deletes
  products by moving them onto themselves (plans/filing-areas-design.md, 1.2);
- on **Linux** it is ``/Storage/mast-share/MAST``, the control host's live share;
- on **macOS** there is no layout at all, and the import raises.

`isolate_filer` replaces ``Filer.__init__`` with one giving each role a folder of its own --
never one folder for all of them -- and setting every attribute the real one sets. It is
called from a suite's conftest, at import, before collection imports anything.

The tests that exist to check the real layout get it through `real_filer_init` /
`real_filer`. ``from common.testing import filer as filer_testing`` keeps the name clear of
``common.filer``.
"""

from __future__ import annotations

import atexit
import os
import platform
import shutil
import socket
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import common.filer as filer_module

#: Captured at import, before `isolate_filer` replaces it.
_REAL_INIT = filer_module.Filer.__init__
_installed = False


def isolate_filer(prefix: str = "mast-tests-") -> None:
    """Point every ``Filer`` at temporary folders, one per role. Idempotent; removed at exit.

    The share is laid out as on Windows: ``share_root`` is the share, ``shared`` and
    ``machine`` this machine's folder inside it.
    """
    global _installed
    if _installed:
        return
    _installed = True

    tmp_root = tempfile.mkdtemp(prefix=prefix)
    atexit.register(shutil.rmtree, tmp_root, ignore_errors=True)

    def _location(*parts: str) -> filer_module.Location:
        path = os.path.join(tmp_root, *parts)
        os.makedirs(path, exist_ok=True)
        return filer_module.Location(path)

    hostname = socket.gethostname().split(".")[0]
    local, ram = _location("local"), _location("ram")
    share_root, shared = _location("share"), _location("share", hostname)
    # Inside `local`, as C:/MAST/ram-fallback/ and C:/MAST/share-fallback/ are on a unit.
    ram_fallback, share_fallback = _location("local", "ram-fallback"), _location("local", "share-fallback")

    def _isolated_init(self, logger=None):
        # Every attribute the real __init__ binds, or the first `init_log` -- which asks for
        # `machine_log_root()`, reading `share_root` and `machine` -- fails at collection.
        self.local = local
        self.shared = shared
        self.ram = ram
        self.share_root = share_root
        self.machine = shared
        self.ram_fallback = ram_fallback
        self.share_fallback = share_fallback
        self.logger = logger

    filer_module.Filer.__init__ = _isolated_init


@contextmanager
def real_filer_init() -> Iterator[None]:
    """The real ``Filer.__init__`` for the duration, for tests of the layout itself.

    Combine it with patches of ``platform.system`` / ``socket.gethostname`` to build the
    layout of another platform; without them it is this machine's real one.
    """
    with patch.object(filer_module.Filer, "__init__", _REAL_INIT):
        yield


def real_filer() -> filer_module.Filer:
    """This machine's real ``Filer``. Skips on macOS, which has no layout to test."""
    import pytest

    if platform.system() == "Darwin":
        pytest.skip("Filer has no macOS layout")
    with real_filer_init():
        return filer_module.Filer()
