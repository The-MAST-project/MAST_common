"""`Filer.ensure_shared_root` -- create this machine's product root on the share.

`shared.root` is `Z:/MAST/<hostname>/` on Windows, and nothing ever created it: not
Filer, not provisioning. A machine whose directory is absent -- a new one, or one that
was renamed -- defers every product forever, behind an INFO-level log line.

That is not hypothetical. `mast-ns-spec` was renamed from `mast-wis-spec`, its directory
on the share went with the old name, and from then until 2026-08-18 every exposure sat on
a volatile ram disk: nine from 2026-06-30, more from 2026-07-01, three from that morning.
All of them moved within seconds of the directory being created by hand -- the write-ahead
queue and its sweeper had held the intents the whole time.

The safety property under test is the other half: the directory is created only when the
*share* is demonstrably mounted. Creating a local `Z:/...` stand-in when the share is down
is the failure that lost frames on 2026-07-14.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("common.filer", reason="filer import chain unavailable")
from common.filer import Filer, FilerTop, Location


class RecordingPool:
    """Captures submitted moves instead of running them, so tests never wait on a thread."""

    def __init__(self):
        self.submitted = []

    def submit(self, *args, **_kwargs):
        self.submitted.append(args)


def _filer(tmp_path, monkeypatch, *, share_exists: bool, per_machine: bool = True):
    """A Filer whose roots are temp directories, with the per-machine root NOT created."""
    ram = tmp_path / "ram"
    ram.mkdir()
    local = tmp_path / "local"
    local.mkdir()
    share = tmp_path / "share"
    if share_exists:
        share.mkdir()
    # Deliberately never created: this is what ensure_shared_root() is for.
    shared = share / "mast-test-host" if per_machine else share

    def fake_init(self, logger=None):
        # Posix-style roots, as in production: `Location("Z:/", "MAST/host/")` joins that way.
        self.ram = Location(None, f"{ram.as_posix()}/")
        self.shared = Location(None, f"{shared.as_posix()}/")
        self.local = Location(None, f"{local.as_posix()}/")
        self.share_root = Location(None, f"{share.as_posix()}/")
        self.tops = {FilerTop.Local: self.local, FilerTop.Shared: self.shared, FilerTop.Ram: self.ram}
        self.logger = logger

    monkeypatch.setattr(Filer, "__init__", fake_init)
    with Filer._pending_lock:
        Filer._pending.clear()
        Filer._in_flight.clear()
    monkeypatch.setattr(Filer, "_mover_pool", None)
    monkeypatch.setattr(Filer, "_ensure_sweeper", lambda self: None)

    instance = Filer()
    instance.ram_dir, instance.shared_dir, instance.share_dir = ram, shared, share
    return instance


class TestEnsureSharedRoot:
    def test_creates_the_per_machine_root_when_the_share_is_up(self, tmp_path, monkeypatch):
        filer = _filer(tmp_path, monkeypatch, share_exists=True)
        assert not filer.shared_dir.exists(), "fixture must start with the root missing"

        assert filer.ensure_shared_root() is True
        assert filer.shared_dir.is_dir(), "the machine's product root should now exist on the share"

    def test_does_not_create_anything_when_the_share_is_down(self, tmp_path, monkeypatch):
        """The 2026-07-14 failure: a local stand-in named like the share loses frames."""
        filer = _filer(tmp_path, monkeypatch, share_exists=False)

        assert filer.ensure_shared_root() is False
        assert not filer.share_dir.exists(), "an absent share must not be conjured locally"
        assert not filer.shared_dir.exists()

    def test_is_idempotent_when_the_root_already_exists(self, tmp_path, monkeypatch):
        filer = _filer(tmp_path, monkeypatch, share_exists=True)
        filer.shared_dir.mkdir(parents=True)

        assert filer.ensure_shared_root() is True
        assert filer.shared_dir.is_dir()

    def test_never_creates_the_share_itself(self, tmp_path, monkeypatch):
        """Z: unmapped, or Linux, where `shared` IS the share: there is no per-machine
        component to add, and creating the mount point would mask an unmounted share."""
        filer = _filer(tmp_path, monkeypatch, share_exists=False, per_machine=False)

        assert filer.ensure_shared_root() is False
        assert not filer.share_dir.exists()


class TestMoveUsesIt:
    def test_a_missing_root_no_longer_defers_the_move(self, tmp_path, monkeypatch):
        """The reported symptom: every product deferred, indefinitely, share up all along."""
        filer = _filer(tmp_path, monkeypatch, share_exists=True)
        pool = RecordingPool()
        monkeypatch.setattr(Filer, "_mover_pool", pool)
        src = tmp_path / "ram" / "exposure-001.fits"
        src.write_text("data")

        filer.move_ram_to_shared(os.path.realpath(str(src)))

        assert filer.shared_dir.is_dir(), "the root should have been created on the way"
        assert len(pool.submitted) == 1, "the move should have been submitted, not deferred"

    def test_a_down_share_still_defers(self, tmp_path, monkeypatch):
        """The behaviour that must survive: queued in ram, for the sweeper to retry."""
        filer = _filer(tmp_path, monkeypatch, share_exists=False)
        pool = RecordingPool()
        monkeypatch.setattr(Filer, "_mover_pool", pool)
        src = tmp_path / "ram" / "exposure-001.fits"
        src.write_text("data")
        real_src = os.path.realpath(str(src))

        filer.move_ram_to_shared(real_src)

        assert pool.submitted == [], "nothing should be submitted against a dead share"
        assert real_src in Filer._pending, "the intent must stay queued for the sweeper"
        assert src.exists(), "the file must stay put in ram"
