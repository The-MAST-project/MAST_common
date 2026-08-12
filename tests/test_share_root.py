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

from common.filer import Filer


@pytest.fixture
def filer():
    return Filer()


class TestShareRoot:
    def test_it_is_above_the_per_machine_product_tree(self, filer):
        """`shared.root` is where THIS machine's products go; `share_root` is the share."""
        share = PurePath(filer.share_root.root)
        shared = PurePath(filer.shared.root)
        assert share != shared or platform.system() == "Linux", (
            "on Windows the two must differ -- shared.root carries the hostname"
        )
        assert str(shared).startswith(str(share)), f"{shared} should sit under {share}"

    def test_it_does_not_carry_the_hostname(self, filer):
        import socket

        assert socket.gethostname().lower() not in filer.share_root.root.lower()

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows layout")
    def test_windows_layout(self, filer):
        assert filer.share_root.root.replace("\\", "/") == "Z:/MAST/"

    @pytest.mark.skipif(platform.system() != "Linux", reason="Linux layout")
    def test_linux_layout(self, filer):
        assert filer.share_root.root == "/Storage/mast-share/MAST"

    def test_the_parent_of_shared_root_is_not_a_substitute(self, filer):
        """The trap this attribute exists to avoid.

        On Windows, parent-of-shared.root happens to equal share_root. On Linux it is
        one level ABOVE the share, because shared.root has no hostname to strip. Code
        written against the Windows behaviour would silently site files outside the
        share on the control and gui hosts.
        """
        parent = PurePath(filer.shared.root).parent
        if platform.system() == "Linux":
            assert str(parent) != filer.share_root.root, (
                "if these ever agree on Linux the asymmetry has been fixed and this test "
                "should be revisited -- but until then they must not be used interchangeably"
            )


class TestNoSilentFallback:
    def test_share_root_does_not_fall_back_to_the_local_disk(self, filer):
        """`local`, `shared` and `ram` all fall back to `C:/MAST/` when their drive is
        unmapped. `share_root` must not: handing back a local path for "the share" is
        how frames were lost on 2026-07-14, and a vault read from there would be a
        different file than the one being asked for."""
        if platform.system() != "Windows":
            pytest.skip("the fallback only exists on Windows")
        assert filer.share_root.root.replace("\\", "/") != filer.local.root.replace("\\", "/")
