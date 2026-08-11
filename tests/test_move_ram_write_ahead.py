"""Tests for `move_ram_to_shared`'s write-ahead record (issue #52).

The bug: the accessible-share fast path started a thread per file and had no rescue if
`Thread.start()` itself raised -- which it does at interpreter shutdown, and under thread
exhaustion. On that path the file was neither moved nor queued: nothing retried it, and on
a unit's volatile RAM disk the artifact was simply gone.

The fix inverts the order. `move_ram_to_shared` records the intent *before* acting, and the
record is cleared only once the source is gone. So every failure mode -- share down, move
erroring, mover never starting, thread killed mid-move -- leaves the entry behind for the
sweeper, rather than each needing its own rescue path.

These tests drive the queue directly rather than waiting on the 30 s sweeper, so they stay
fast and deterministic; `test_release_folder.py` covers the sweep end to end.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("common.filer", reason="filer import chain unavailable")
import common.filer as filer_module
from common.filer import Filer, FilerTop, Location, MoveGuardian


class RefusingPool:
    """Stands in for a pool that will not take work -- what `submit` does once the
    interpreter is shutting down, and what `Thread.start()` did in the reported traceback."""

    def __init__(self, message="cannot schedule new futures after interpreter shutdown"):
        self.message = message

    def submit(self, *_args, **_kwargs):
        raise RuntimeError(self.message)


@pytest.fixture
def filer(tmp_path, monkeypatch):
    """A Filer with ram/shared pointed at temp directories, on any platform."""
    ram = tmp_path / "ram"
    shared = tmp_path / "shared"
    ram.mkdir()
    shared.mkdir()

    def fake_init(self, logger=None):
        # Posix-style, because that is what production roots look like on every platform:
        # `Location("D:/", "MAST/")` joins to "D:/MAST/". `move_ram_to_shared` derives the
        # destination by replacing that prefix in the posix spelling of the source, so a
        # root written with native separators would silently never match on Windows and
        # every file would be "moved" to itself.
        self.ram = Location(None, f"{ram.as_posix()}/")
        self.shared = Location(None, f"{shared.as_posix()}/")
        self.local = self.shared
        self.tops = {FilerTop.Local: self.local, FilerTop.Shared: self.shared, FilerTop.Ram: self.ram}
        self.logger = logger

    monkeypatch.setattr(Filer, "__init__", fake_init)

    with Filer._pending_lock:
        Filer._pending.clear()
        Filer._in_flight.clear()
    monkeypatch.setattr(Filer, "_mover_pool", None)
    # Never start the real sweeper: these tests assert on the queue, and a 30 s background
    # thread would race them.
    monkeypatch.setattr(Filer, "_ensure_sweeper", lambda self: None)

    instance = Filer()
    instance.ram_dir, instance.shared_dir = ram, shared
    return instance


def _make(ram, name="exposure-001.fits", content="data"):
    """Create a source file and return it spelled the way the queue keys it.

    `move_ram_to_shared` keys `_pending` by `os.path.realpath`, matching MoveGuardian and
    the `os.sep` comparison in `_is_under`. Asserting against a raw `str(path)` passes on
    Linux and fails on Windows, where the two spellings differ -- which is how the
    separator inconsistency this fixture now mirrors was found.
    """
    path = ram / name
    path.write_text(content)
    return os.path.realpath(str(path))


def test_refused_mover_leaves_the_file_queued(filer, monkeypatch):
    """The reported bug: the mover cannot start, so the record must already exist."""
    src = _make(filer.ram_dir)
    monkeypatch.setattr(Filer, "_mover_pool", RefusingPool())

    filer.move_ram_to_shared(src)

    assert os.path.exists(src), "file must stay put when the mover never ran"
    recorded = Filer._pending.get(src, "")
    assert recorded, "the intent must be recorded"
    assert os.path.realpath(recorded).startswith(os.path.realpath(str(filer.shared_dir))), (
        "the recorded destination must be on the shared side"
    )
    assert src not in Filer._in_flight, "a refused submit must not leave the source marked in flight"


def test_unreachable_share_leaves_the_file_queued(filer, monkeypatch):
    """The path that already worked, still working -- via the same single mechanism."""
    monkeypatch.setattr(filer_module, "is_accessible", lambda *_a, **_k: False)
    src = _make(filer.ram_dir)

    filer.move_ram_to_shared(src)

    assert os.path.exists(src)
    assert src in Filer._pending


def test_successful_move_clears_the_record(filer):
    """The record is transient: a completed move must not leave the queue growing, or
    `release_folder` would wait for ever on a folder that is genuinely drained."""
    src = _make(filer.ram_dir)

    filer.move_ram_to_shared(src)
    Filer._mover_pool.shutdown(wait=True)  # deterministic: wait for the pool, not a sleep

    assert not os.path.exists(src)
    assert (filer.shared_dir / "exposure-001.fits").read_text() == "data"
    assert src not in Filer._pending
    assert src not in Filer._in_flight


def test_failed_move_keeps_the_record(filer, monkeypatch):
    """A move that errors leaves the source behind, so the entry must survive for a retry."""
    src = _make(filer.ram_dir)
    monkeypatch.setattr(Filer, "move", lambda self, s, d: None)  # pretend it silently failed

    filer.move_ram_to_shared(src)
    Filer._mover_pool.shutdown(wait=True)

    assert os.path.exists(src)
    assert src in Filer._pending, "a surviving source must stay queued"
    assert src not in Filer._in_flight


def test_sweeper_skips_sources_a_mover_is_handling(filer):
    """Recording intent up front means a queued entry can also be in flight. The sweeper
    must leave those alone, or two movers race on the same source."""
    src = _make(filer.ram_dir)
    with Filer._pending_lock:
        Filer._pending[src] = str(filer.shared_dir / "exposure-001.fits")
        Filer._in_flight.add(src)

    filer._drain_pending()

    assert os.path.exists(src), "the sweeper must not touch a source already being moved"
    assert src in Filer._pending


def test_queued_move_is_visible_to_folder_drained(filer, monkeypatch):
    """A queued move must block `release_folder` from reaping the folder.

    `_folder_drained` matches with `_is_under`, which compares using `os.sep`, and
    `_protected`/`_products` are keyed by `os.path.realpath`. Keying `_pending` any other
    way makes this check silently never match on Windows -- the folder then looks drained
    while moves are still outstanding. Passes either way on Linux, where the two spellings
    coincide; it is the Windows leg of CI that has teeth here.
    """
    monkeypatch.setattr(filer_module, "is_accessible", lambda *_a, **_k: False)
    folder = filer.ram_dir / "acq-001"
    folder.mkdir()
    src = _make(folder, "e1.fits")

    filer.move_ram_to_shared(src)
    drained, why, blockers = MoveGuardian()._folder_drained(os.path.realpath(str(folder)))

    assert not drained, "a folder with a queued move is not drained"
    assert "queued" in why
    assert src in blockers


def test_movers_are_bounded(filer):
    """The other route into the reported RuntimeError was thread exhaustion: one unbounded
    thread per file. The pool must cap them however many files arrive."""
    sources = [_make(filer.ram_dir, f"exposure-{i:03}.fits") for i in range(40)]

    filer.move_ram_to_shared(sources)
    pool = Filer._mover_pool
    pool.shutdown(wait=True)

    assert pool._max_workers == Filer._MOVER_WORKERS
    assert len(pool._threads) <= Filer._MOVER_WORKERS, "one thread per file would be 40"
    assert not any(os.path.exists(s) for s in sources)
    assert not Filer._pending
