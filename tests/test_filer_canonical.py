"""Comparing filing-area roots must not fail when a root cannot be resolved.

On the supervisor's dev VM, 2026-11-17: the share answered SMB but refused the unit's
credentials, so `os.path.realpath` on the share root raised `OSError` (WinError 1326, which
`ntpath`'s non-strict mode does not tolerate). `start_product_relocation_sweep` compares roots
through `_canonical`, the error escaped the app's lifespan, and uvicorn exited: the unit app
could not start at all, where the Filer is meant to fall back to the local disk.
"""

from __future__ import annotations

import os

import pytest

from common import filer


@pytest.fixture
def unresolvable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """After ``tmp_path``, which resolves its own path."""

    def refuse(path, *args, **kwargs):
        raise OSError(1326, "The user name or password is incorrect", str(path))

    monkeypatch.setattr(filer.os.path, "realpath", refuse)


@pytest.mark.usefixtures("unresolvable")
def test_an_unresolvable_root_is_compared_by_its_absolute_path() -> None:
    share = "//share-host/mast-share/MAST/unit"

    assert filer._canonical(share) == os.path.normcase(os.path.abspath(share))


@pytest.mark.usefixtures("unresolvable")
def test_overlap_of_an_unresolvable_root_is_still_answered(tmp_path) -> None:
    assert filer._overlaps(tmp_path, tmp_path / "inner")
    assert not filer._overlaps(tmp_path / "a", tmp_path / "b")
