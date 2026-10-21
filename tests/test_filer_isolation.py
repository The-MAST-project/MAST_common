"""The suite's ``Filer`` is on temporary folders, one per role, on every platform.

What `common.testing.filer.isolate_filer` (installed by conftest) promises, pinned so that
the suite can never again reach a machine's real RAM disk or share, nor collapse the roles
onto one folder (plans/filing-areas-design.md, 1.2 and 13).
"""

from __future__ import annotations

import os
import tempfile

from common.filer import Filer
from common.utils import filer as module_level_filer


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def test_every_root_is_under_the_temporary_directory():
    filer = Filer()
    tmp = _norm(tempfile.gettempdir())
    for root in (filer.local.root, filer.ram.root, filer.shared.root, filer.share_root.root, filer.machine.root):
        assert _norm(root).startswith(tmp), root


def test_the_roles_are_distinct_folders():
    filer = Filer()
    roots = {_norm(filer.local.root), _norm(filer.ram.root), _norm(filer.shared.root)}
    assert len(roots) == 3, roots


def test_shared_is_this_machines_folder_inside_the_share_as_on_windows():
    filer = Filer()
    assert _norm(filer.shared.root).startswith(_norm(filer.share_root.root))
    assert _norm(filer.shared.root) != _norm(filer.share_root.root)
    assert filer.machine.root == filer.shared.root


def test_the_module_level_filer_is_isolated_too():
    # common.utils builds it at import; conftest has to have run first.
    assert _norm(module_level_filer.ram.root).startswith(_norm(tempfile.gettempdir()))
