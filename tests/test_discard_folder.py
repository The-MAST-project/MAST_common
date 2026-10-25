"""MoveGuardian.discard_folder -- throwing away a scratch folder and everything kept about it.

A solver writes its result inside `protect`, in its own scratch folder. That claims the
folder (an open handle, and a `.<name>.lock` beside it) and records the file as a product.
Removing the folder with a bare `rmtree` left all three behind, once per solve: on mast00
(2026-10-25) every solve left a lock file in `D:/MAST/tmp`.
"""

from __future__ import annotations

import os
import shutil
import threading
import time

import pytest

from common.filer import MoveGuardian


@pytest.fixture(autouse=True)
def guardian():
    g = MoveGuardian()
    for real in list(MoveGuardian._folder_claims):
        g._release_claim(real)
    with MoveGuardian._condition:
        MoveGuardian._products.clear()
        MoveGuardian._protected.clear()
        MoveGuardian._moving.clear()
    yield g
    for real in list(MoveGuardian._folder_claims):
        g._release_claim(real)
    with MoveGuardian._condition:
        MoveGuardian._products.clear()


@pytest.fixture
def scratch(tmp_path):
    d = tmp_path / "tmp" / "tmp_ABCDEFGHIJK"
    d.mkdir(parents=True)
    return d


def _lock_of(folder) -> str:
    real = os.path.realpath(str(folder))
    return os.path.join(os.path.dirname(real), f".{os.path.basename(real)}.lock")


def _solve_into(guardian, scratch):
    """As MastrometryDotNet.solve does: a result written under protect, intermediates not."""
    (scratch / "frame.axy").write_bytes(b"x")
    result = scratch / "frame,solver=x-result.txt"
    with guardian.protect(str(result)), open(result, "w") as f:
        f.write("solved")
    return result


class TestDiscard:
    def test_the_folder_goes(self, guardian, scratch):
        _solve_into(guardian, scratch)
        guardian.discard_folder(scratch)
        assert not scratch.exists()

    def test_and_its_lock_file_and_claim(self, guardian, scratch):
        _solve_into(guardian, scratch)
        assert os.path.exists(_lock_of(scratch)), "protect() claimed the folder"

        guardian.discard_folder(scratch)

        assert not os.path.exists(_lock_of(scratch))
        assert os.path.realpath(str(scratch)) not in MoveGuardian._folder_claims

    def test_and_its_product_records(self, guardian, scratch):
        result = _solve_into(guardian, scratch)
        assert os.path.realpath(str(result)) in MoveGuardian._products

        guardian.discard_folder(scratch)

        assert not any(p.startswith(os.path.realpath(str(scratch))) for p in MoveGuardian._products)

    def test_nothing_left_beside_it_after_many(self, guardian, tmp_path):
        parent = tmp_path / "tmp"
        for i in range(5):
            d = parent / f"tmp_{i:011d}"
            d.mkdir(parents=True)
            _solve_into(guardian, d)
            guardian.discard_folder(d)
        assert os.listdir(parent) == []

    def test_a_folder_already_gone_still_releases(self, guardian, scratch):
        _solve_into(guardian, scratch)
        shutil.rmtree(scratch)
        guardian.discard_folder(scratch)
        assert not os.path.exists(_lock_of(scratch))
        assert os.path.realpath(str(scratch)) not in MoveGuardian._folder_claims

    def test_it_waits_for_a_write_in_progress(self, guardian, scratch):
        result = scratch / "late.txt"
        entered, release = threading.Event(), threading.Event()

        def writer():
            with guardian.protect(str(result)):
                entered.set()
                release.wait(5)
                result.write_text("done")

        t = threading.Thread(target=writer)
        t.start()
        assert entered.wait(5)
        done = threading.Event()
        threading.Thread(target=lambda: (guardian.discard_folder(scratch), done.set())).start()
        time.sleep(0.2)
        assert not done.is_set(), "it must not remove a folder mid-write"
        release.set()
        t.join(5)
        assert done.wait(5)
        assert not scratch.exists()
