"""Moving a folder onto an existing folder must MERGE, not nest.

`shutil.move` has a semantic that differs from a rename: when the destination already
exists as a directory it moves the source INSIDE it, producing `<dst>/<src.name>`. That
is how mast00 grew `Acquisitions/Acquisitions` and six `spec/spec` directories, with one
night's products split across two levels so that anything reading `<acq>/spec/*.fits`
saw a partial set.

The flaw was latent for as long as `Filer.move` only ever handled files, whose
destination is a full path that does not exist yet. `_relocate_products` is the first
caller to pass folders, which is what turned it into an active bug.
"""

from __future__ import annotations

import pytest

from common.filer import Filer, FilerTop


@pytest.fixture
def filer(tmp_path, monkeypatch):
    """A Filer whose ram and shared areas are two directories under tmp_path."""
    ram, shared = tmp_path / "ram", tmp_path / "shared"
    ram.mkdir()
    shared.mkdir()
    f = Filer()
    location = type("Location", (), {})
    f.ram = location()
    f.ram.root = ram.as_posix() + "/"
    f.shared = location()
    f.shared.root = shared.as_posix() + "/"
    f.local = f.shared
    f.tops = {FilerTop.Local: f.local, FilerTop.Shared: f.shared, FilerTop.Ram: f.ram}
    f.ram_path, f.shared_path = ram, shared
    return f


def write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestMergingRatherThanNesting:
    def test_a_folder_moved_onto_an_existing_folder_merges(self, filer):
        write(filer.ram_path / "spec" / "new.fits")
        write(filer.shared_path / "spec" / "old.fits")

        filer.move(filer.ram_path / "spec", filer.shared_path / "spec")

        assert not (filer.shared_path / "spec" / "spec").exists(), "the source must not be nested inside"
        assert (filer.shared_path / "spec" / "new.fits").exists(), "the moved file must land beside the existing one"
        assert (filer.shared_path / "spec" / "old.fits").exists(), "what was already there must survive"
        assert not (filer.ram_path / "spec").exists(), "the ram-side folder must be gone, so the disk is reclaimed"

    def test_the_mast00_shape_is_reproduced_and_fixed(self, filer):
        """The exact tree that produced `spec/spec`: an acquisition folder whose `spec`
        exists on both sides."""
        acq = "seq=0001,time=23-06-04_959,target=16.7,26.7"
        write(filer.ram_path / "Acquisitions" / acq / "spec" / "seq=0003.fits")
        write(filer.ram_path / "Acquisitions" / "seq.txt")
        write(filer.shared_path / "Acquisitions" / acq / "spec" / "seq=0001.fits")

        filer.move(filer.ram_path / "Acquisitions", filer.shared_path / "Acquisitions")

        spec = filer.shared_path / "Acquisitions" / acq / "spec"
        assert not (filer.shared_path / "Acquisitions" / "Acquisitions").exists()
        assert not (spec / "spec").exists()
        assert {p.name for p in spec.glob("*.fits")} == {"seq=0001.fits", "seq=0003.fits"}, (
            "both nights' products must sit in ONE spec folder"
        )
        # This used to assert the counter was carried across. That was wrong: the
        # destination maintains its own, so moving it is a guaranteed collision -- observed
        # on mast00 on 2026-08-17 erroring twice per sweep, every 30 seconds, and leaving
        # the ram tree undrainable. It stays put.
        assert (filer.ram_path / "Acquisitions" / "seq.txt").exists(), "the counter stays on the ram side"
        assert not (filer.shared_path / "Acquisitions" / "seq.txt").exists(), "and is not carried across"
        assert (filer.ram_path / "Acquisitions").exists(), "so its folder legitimately survives"
        assert not (filer.ram_path / "Acquisitions" / acq).exists(), "but the products are gone"

    def test_nested_folders_merge_at_every_level(self, filer):
        write(filer.ram_path / "a" / "b" / "c" / "from_ram.txt")
        write(filer.shared_path / "a" / "b" / "c" / "from_shared.txt")

        filer.move(filer.ram_path / "a", filer.shared_path / "a")

        c = filer.shared_path / "a" / "b" / "c"
        assert {p.name for p in c.iterdir()} == {"from_ram.txt", "from_shared.txt"}
        assert not (c / "c").exists() and not (filer.shared_path / "a" / "a").exists()


class TestUnchangedBehaviour:
    def test_a_folder_moved_to_a_free_name_is_a_plain_move(self, filer):
        write(filer.ram_path / "spec" / "a.fits")

        filer.move(filer.ram_path / "spec", filer.shared_path / "spec")

        assert (filer.shared_path / "spec" / "a.fits").exists()
        assert not (filer.shared_path / "spec" / "spec").exists()

    def test_files_still_move_as_before(self, filer):
        write(filer.ram_path / "one.fits", "data")

        filer.move(filer.ram_path / "one.fits", filer.shared_path / "sub" / "one.fits")

        assert (filer.shared_path / "sub" / "one.fits").read_text() == "data"
        assert not (filer.ram_path / "one.fits").exists()


class TestCollisions:
    """A name present on both sides as anything but two folders is two distinct
    products. Leaving the source is recoverable -- the next sweep retries it.
    Overwriting is not."""

    def test_a_clashing_file_is_left_alone_not_overwritten(self, filer):
        write(filer.ram_path / "spec" / "same.fits", "from ram")
        write(filer.shared_path / "spec" / "same.fits", "from shared")

        filer.move(filer.ram_path / "spec", filer.shared_path / "spec")

        assert (filer.shared_path / "spec" / "same.fits").read_text() == "from shared", "must not be overwritten"
        assert (filer.ram_path / "spec" / "same.fits").read_text() == "from ram", "must not be destroyed"

    def test_a_collision_does_not_block_the_other_files(self, filer):
        write(filer.ram_path / "spec" / "same.fits", "from ram")
        write(filer.ram_path / "spec" / "unique.fits", "moves fine")
        write(filer.shared_path / "spec" / "same.fits", "from shared")

        filer.move(filer.ram_path / "spec", filer.shared_path / "spec")

        assert (filer.shared_path / "spec" / "unique.fits").exists(), "one clash must not strand the rest"
        assert (filer.ram_path / "spec").exists(), "the source folder stays, holding only what could not move"
        assert {p.name for p in (filer.ram_path / "spec").iterdir()} == {"same.fits"}
