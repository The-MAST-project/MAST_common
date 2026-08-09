"""Tests for MoveGuardian's core contract: writes and moves never overlap.

This is the primitive that keeps ``Filer.move`` from shipping a half-written FITS, and it
had no tests. Both of its failure modes are expensive and neither is obvious from a log:
too little exclusion silently corrupts a frame, too much wedges the mover threads and the
ram disk fills (which is how the 2026-08-04 night was first misdiagnosed).

The interesting rule is that overlap is *bidirectional* -- protecting a file must block a
move of its parent directory, and protecting a directory must block a move of anything
underneath it -- while paths that merely share a textual prefix (``/a/b`` vs ``/a/bc``)
must not block each other at all.

Every blocking claim is taken on a daemon thread with a timeout, so a regression that
reintroduces a deadlock fails the suite instead of hanging it.
"""

from __future__ import annotations

import os
from threading import Event, Thread

import pytest

pytest.importorskip("common.filer", reason="filer import chain unavailable")
from common.filer import Filer, MoveGuardian

# Long enough that a loaded CI box does not produce a false failure, short enough that a
# real deadlock fails the run quickly.
SETTLE = 0.4
PATIENCE = 5.0


@pytest.fixture(autouse=True)
def guardian():
    g = MoveGuardian()
    with MoveGuardian._condition:
        MoveGuardian._protected.clear()
        MoveGuardian._moving.clear()
        MoveGuardian._products.clear()
    yield g
    with MoveGuardian._condition:
        MoveGuardian._protected.clear()
        MoveGuardian._moving.clear()
        MoveGuardian._products.clear()


class Claim:
    """Takes a guardian claim on its own thread, so a blocked entry cannot hang the test."""

    def __init__(self, make_claim):
        self.entered = Event()
        self._release = Event()
        self._thread = Thread(target=self._run, args=(make_claim,), daemon=True)
        self._thread.start()

    def _run(self, make_claim):
        with make_claim():
            self.entered.set()
            self._release.wait(timeout=PATIENCE)

    def wait_entered(self, timeout=PATIENCE) -> bool:
        return self.entered.wait(timeout)

    def release(self):
        self._release.set()
        self._thread.join(timeout=PATIENCE)
        assert not self._thread.is_alive(), "claim thread did not finish"


@pytest.fixture
def paths(tmp_path):
    folder = tmp_path / "acq" / "spec"
    folder.mkdir(parents=True)
    return {
        "folder": str(folder),
        "file": str(folder / "seq=0001.fits"),
        "sibling": str(folder / "seq=0002.fits"),
        "parent": str(tmp_path / "acq"),
    }


class TestMutualExclusion:
    def test_protect_blocks_an_overlapping_move(self, guardian, paths):
        writer = Claim(lambda: guardian.protect(paths["file"]))
        assert writer.wait_entered()

        mover = Claim(lambda: guardian.moving(paths["file"]))
        assert not mover.wait_entered(SETTLE), "a move must wait while the path is being written"

        writer.release()
        assert mover.wait_entered(), "the move must proceed once the write finishes"
        mover.release()

    def test_move_blocks_an_overlapping_protect(self, guardian, paths):
        """The reverse direction: a producer must not start writing into a live move."""
        mover = Claim(lambda: guardian.moving(paths["file"]))
        assert mover.wait_entered()

        writer = Claim(lambda: guardian.protect(paths["file"]))
        assert not writer.wait_entered(SETTLE), "a write must wait while the path is being moved"

        mover.release()
        assert writer.wait_entered(), "the write must proceed once the move finishes"
        writer.release()

    def test_unrelated_paths_do_not_block(self, guardian, paths):
        writer = Claim(lambda: guardian.protect(paths["file"]))
        assert writer.wait_entered()

        mover = Claim(lambda: guardian.moving(paths["sibling"]))
        assert mover.wait_entered(), "moves of unrelated files must run concurrently with writes"
        mover.release()
        writer.release()

    def test_two_moves_of_the_same_path_do_not_block_each_other(self, guardian, paths):
        """Documents current behaviour: `moving` waits on `_protected` only, so two movers
        can claim one path. Harmless today because Filer.move re-checks existence, but it
        is a real property -- if it ever changes, this test should be the one to say so."""
        first = Claim(lambda: guardian.moving(paths["file"]))
        assert first.wait_entered()
        second = Claim(lambda: guardian.moving(paths["file"]))
        assert second.wait_entered(SETTLE)
        second.release()
        first.release()


class TestOverlapIsBidirectional:
    def test_protecting_a_file_blocks_moving_its_folder(self, guardian, paths):
        writer = Claim(lambda: guardian.protect(paths["file"]))
        assert writer.wait_entered()

        mover = Claim(lambda: guardian.moving(paths["folder"]))
        assert not mover.wait_entered(SETTLE), "moving a folder must wait for writes underneath it"

        writer.release()
        assert mover.wait_entered()
        mover.release()

    def test_protecting_a_folder_blocks_moving_a_file_under_it(self, guardian, paths):
        writer = Claim(lambda: guardian.protect(paths["folder"]))
        assert writer.wait_entered()

        mover = Claim(lambda: guardian.moving(paths["file"]))
        assert not mover.wait_entered(SETTLE), "a protected folder must block moves beneath it"

        writer.release()
        assert mover.wait_entered()
        mover.release()

    def test_a_shared_prefix_is_not_an_overlap(self, guardian, tmp_path):
        """'/a/b' and '/a/bc' overlap textually but not as paths. Guarded by the `+ os.sep`
        in _conflicts; without it every move would block on unrelated neighbours."""
        b = tmp_path / "b"
        bc = tmp_path / "bc"
        b.mkdir()
        bc.mkdir()

        writer = Claim(lambda: guardian.protect(str(b)))
        assert writer.wait_entered()

        mover = Claim(lambda: guardian.moving(str(bc)))
        assert mover.wait_entered(), "'bc' must not be treated as living under 'b'"
        mover.release()
        writer.release()


class TestConflicts:
    """_conflicts is the whole overlap rule; exercise it directly, without threads."""

    @pytest.mark.parametrize(
        ("registered", "queried", "expected"),
        [
            ("/a/b", "/a/b", True),  # equal
            ("/a/b/c.fits", "/a/b", True),  # registered is under queried
            ("/a/b", "/a/b/c.fits", True),  # queried is under registered
            ("/a/bc", "/a/b", False),  # shared prefix, different name
            ("/a/b", "/a/bc", False),
            ("/a/b", "/x/y", False),  # unrelated
        ],
        ids=["equal", "under", "above", "prefix-longer", "prefix-shorter", "unrelated"],
    )
    def test_overlap_rule(self, registered, queried, expected):
        registered = os.path.normpath(registered)
        queried = os.path.normpath(queried)
        assert bool(MoveGuardian._conflicts(queried, {registered: 1})) is expected


class TestRefcounting:
    def test_nested_claims_of_one_path_release_once(self, guardian, paths):
        real = os.path.realpath(paths["file"])
        with guardian.protect(paths["file"]):
            assert MoveGuardian._protected[real] == 1
            with guardian.protect(paths["file"]):
                assert MoveGuardian._protected[real] == 2
            assert MoveGuardian._protected[real] == 1, "inner exit must not drop the outer claim"
        assert real not in MoveGuardian._protected, "registry must be empty once all claims exit"

    def test_registry_is_clean_after_an_exception(self, guardian, paths):
        real = os.path.realpath(paths["file"])
        with pytest.raises(RuntimeError), guardian.protect(paths["file"]):
            raise RuntimeError("boom")
        assert real not in MoveGuardian._protected, "a raising writer must not leak its claim"

    def test_a_claim_covers_every_path_it_was_given(self, guardian, paths):
        with guardian.protect(paths["file"], paths["sibling"]):
            assert guardian.is_protected(paths["file"])
            assert guardian.is_protected(paths["sibling"])
        assert not guardian.is_protected(paths["file"])
        assert not guardian.is_protected(paths["sibling"])


class TestIsProtected:
    def test_reports_equal_under_and_above(self, guardian, paths):
        with guardian.protect(paths["file"]):
            assert guardian.is_protected(paths["file"]), "the path itself"
            assert guardian.is_protected(paths["folder"]), "an ancestor of a protected file"
            assert not guardian.is_protected(paths["sibling"]), "an unrelated sibling"

    def test_false_when_nothing_is_protected(self, guardian, paths):
        assert not guardian.is_protected(paths["file"])


class TestWaitUntilFree:
    def test_returns_immediately_when_free(self, guardian, paths):
        assert guardian.wait_until_free(paths["file"], timeout=SETTLE) is True

    def test_times_out_while_protected(self, guardian, paths):
        writer = Claim(lambda: guardian.protect(paths["file"]))
        assert writer.wait_entered()
        assert guardian.wait_until_free(paths["file"], timeout=SETTLE) is False
        writer.release()

    def test_unblocks_when_the_write_finishes(self, guardian, paths):
        writer = Claim(lambda: guardian.protect(paths["file"]))
        assert writer.wait_entered()

        freed = Event()

        def waiter():
            if guardian.wait_until_free(paths["file"], timeout=PATIENCE):
                freed.set()

        thread = Thread(target=waiter, daemon=True)
        thread.start()
        assert not freed.wait(SETTLE), "must not report free while the write is in progress"

        writer.release()
        assert freed.wait(PATIENCE), "must be woken when the protection is released"
        thread.join(timeout=PATIENCE)

    def test_does_not_claim_the_path(self, guardian, paths):
        """Unlike moving(), wait_until_free only observes -- a writer may start right after."""
        assert guardian.wait_until_free(paths["file"], timeout=SETTLE)
        writer = Claim(lambda: guardian.protect(paths["file"]))
        assert writer.wait_entered(), "wait_until_free must not have left a claim behind"
        writer.release()


class TestFilerMoveIntegration:
    """The promise Filer.move makes: it will not move a file out from under its writer."""

    def test_move_waits_for_the_writer(self, guardian, tmp_path):
        source = tmp_path / "src" / "frame.fits"
        source.parent.mkdir()
        source.write_text("half written")
        destination = tmp_path / "dst" / "frame.fits"

        writer = Claim(lambda: guardian.protect(str(source)))
        assert writer.wait_entered()

        moved = Event()
        filer = Filer()  # absolute paths are passed in, so its configured roots are unused

        def do_move():
            filer.move(str(source), str(destination))
            moved.set()

        mover = Thread(target=do_move, daemon=True)
        mover.start()
        assert not moved.wait(SETTLE), "Filer.move must block while the source is protected"
        assert source.exists(), "the source must still be in place"

        writer.release()
        assert moved.wait(PATIENCE), "the move must complete once the writer is done"
        mover.join(timeout=PATIENCE)
        assert destination.exists() and not source.exists()
