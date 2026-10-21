"""`Filer.share_root` -- the share itself, above every machine's product tree.

Separate from `shared`, which on Windows carries the hostname and on Linux does not.
Anything sited at the share level (the vault, MAST_common#60) has to be anchored on
something that means the same thing everywhere, and "the parent of `shared.root`" does
not: it is right on Windows and one level too high on Linux.
"""

from __future__ import annotations

import platform
from pathlib import PurePath

import pytest

from common.filer import is_windows_drive_mapped
from common.testing.filer import real_filer


@pytest.fixture
def filer():
    # The real layout: the suite's Filer is pointed at temporary folders (common.testing.filer).
    return real_filer()


class TestShareRoot:
    def test_it_is_above_the_per_machine_product_tree(self, filer):
        """`shared.root` is where THIS machine's products go; `share_root` is the share.

        Only checkable where the share is actually present. Without it `shared` falls
        back to the local disk while `share_root` does not -- that asymmetry is the
        point of this attribute, not a violation of it.
        """
        if platform.system() != "Windows" or not is_windows_drive_mapped("Z:"):
            pytest.skip("the per-machine tree only sits under the share when Z: is mapped")

        share = PurePath(filer.share_root.root)
        shared = PurePath(filer.shared.root)
        assert share != shared, "shared.root carries the hostname; share_root must not"
        assert str(shared).startswith(str(share)), f"{shared} should sit under {share}"

    def test_it_keeps_its_value_when_the_share_is_absent(self, filer):
        """The others fall back to `C:/MAST/` when their drive is unmapped. This one
        must not: a caller asking for the share needs to be told where the share is,
        and to fail reading it, rather than to be handed a local path silently."""
        if platform.system() != "Windows":
            pytest.skip("the fallback only exists on Windows")
        assert filer.share_root.root.replace("\\", "/").startswith("Z:/")

    def test_it_does_not_carry_the_hostname(self, filer):
        import socket

        assert socket.gethostname().lower() not in filer.share_root.root.lower()

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows layout")
    def test_windows_layout(self, filer):
        assert filer.share_root.root.replace("\\", "/") == "Z:/MAST/"

    @pytest.mark.skipif(platform.system() != "Linux", reason="Linux layout")
    def test_linux_layout(self, filer):
        assert filer.share_root.root == "/Storage/mast-share/MAST/"

    def test_the_parent_of_shared_root_is_the_share_on_every_platform(self, filer):
        """It was the trap this attribute existed to avoid: on Linux `shared.root` had no
        hostname, so its parent was one level ABOVE the share. With `shared` per-machine
        everywhere (plans/filing-areas-design.md 3.1) they agree -- when the share is up.
        Without it, `shared` falls back to the local disk on Windows and nothing follows."""
        if platform.system() == "Windows" and not is_windows_drive_mapped("Z:"):
            pytest.skip("Z: is not mapped, so shared is the local disk")
        assert PurePath(filer.shared.root).parent == PurePath(filer.share_root.root)


class TestNoSilentFallback:
    def test_share_root_does_not_fall_back_to_the_local_disk(self, filer):
        """`local`, `shared` and `ram` all fall back to `C:/MAST/` when their drive is
        unmapped. `share_root` must not: handing back a local path for "the share" is
        how frames were lost on 2026-07-14, and a vault read from there would be a
        different file than the one being asked for."""
        if platform.system() != "Windows":
            pytest.skip("the fallback only exists on Windows")
        assert filer.share_root.root.replace("\\", "/") != filer.local.root.replace("\\", "/")


class TestWholeShareFolders:
    """Plans and runs belong to the share as a whole -- control's -- not to one machine."""

    def test_plans_are_under_the_share_root_not_this_machines_folder(self):
        from common.filer import Filer
        from common.paths import PathMaker

        filer = Filer()  # the suite's layout: shared is a folder inside share_root
        plans = PurePath(PathMaker.make_plans_folder())
        assert plans.parent == PurePath(filer.share_root.root)

    def test_runs_are_under_the_share_root_not_this_machines_folder(self):
        from common.filer import Filer
        from common.paths import PathMaker

        filer = Filer()
        run = PurePath(PathMaker.make_run_folder())
        assert PurePath(filer.share_root.root) / "runs" in run.parents
        assert PurePath(filer.shared.root) not in run.parents
