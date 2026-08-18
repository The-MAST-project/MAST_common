"""`Filer.machine_log_root` -- where THIS machine's logs go.

The companion to test_share_root.py. That file pins `share_root` (the share itself);
this one pins the per-machine root, which is the thing `accessible_shared_root()` is
not on Linux -- there `shared.root` IS the share root, with no hostname component, so
a Linux host writing its night folders under it lands them at the TOP of the share,
beside every machine's folder rather than inside one.

That is not hypothetical: mast-ns-control, the only Linux machine and the share's
owner, accumulated 89 such `<date>` folders (2025-10-28..2026-08-06, ~500 MB) before
anyone noticed they were not any unit's.

The Linux branch cannot run on a Windows box and vice versa, so the platform-specific
construction is exercised directly rather than through whichever branch happens to be
live -- otherwise the bug this guards against is untestable on the machines we develop on.
"""

from __future__ import annotations

import platform
import socket
from pathlib import PurePath
from unittest.mock import patch

import pytest

from common.filer import Filer, is_windows_drive_mapped


def _filer_as(system: str, hostname: str) -> Filer:
    """A Filer built as though it were running on `system` as `hostname`."""
    with patch("platform.system", return_value=system), patch("socket.gethostname", return_value=hostname):
        return Filer()


class TestItIsPerMachine:
    """The property that matters, on every platform: the log root names this machine."""

    @pytest.mark.parametrize(("system", "hostname"), [("Linux", "mast-ns-control"), ("Windows", "mast00")])
    def test_the_hostname_is_in_the_path(self, system, hostname):
        filer = _filer_as(system, hostname)

        assert hostname in filer.machine.root, (
            f"on {system} the per-machine root must name the machine, else its logs land "
            f"in a folder shared with every other host: {filer.machine.root}"
        )

    @pytest.mark.parametrize(("system", "hostname"), [("Linux", "mast-ns-control"), ("Windows", "mast00")])
    def test_it_sits_strictly_below_the_share(self, system, hostname):
        """A level down from the share, not at it. On Linux these were the same path,
        which is the whole defect."""
        filer = _filer_as(system, hostname)

        share = PurePath(filer.share_root.root)
        machine = PurePath(filer.machine.root)
        assert machine != share, f"the per-machine root must not BE the share root ({share})"
        assert share in machine.parents, f"{machine} should sit under {share}"


class TestTheLinuxRegression:
    """The specific shape of the bug, pinned so it cannot come back."""

    def test_it_is_not_the_share_root_on_linux(self):
        filer = _filer_as("Linux", "mast-ns-control")

        assert filer.machine.root != filer.share_root.root
        assert filer.machine.root == "/Storage/mast-share/MAST/mast-ns-control"

    def test_accessible_shared_root_is_still_not_a_substitute_on_linux(self):
        """Why the new accessor had to exist. If these ever agree, the asymmetry has been
        fixed at the source and this whole module can be reconsidered."""
        filer = _filer_as("Linux", "mast-ns-control")

        assert filer.shared.root != filer.machine.root, (
            "`shared` is the share root on Linux; using it for logs is what put 89 date folders at the top of the share"
        )

    def test_an_fqdn_does_not_leak_into_the_path(self):
        """`socket.gethostname()` may return an FQDN. `get_unit` already splits on '.';
        a log root that did not would create `mast-ns-control.weizmann.ac.il/` beside the
        short-named folder every other tool uses."""
        filer = _filer_as("Linux", "mast-ns-control.weizmann.ac.il")

        assert filer.machine.root == "/Storage/mast-share/MAST/mast-ns-control"


class TestWindowsIsUnchanged:
    """The Windows path already behaved; the change must not disturb it."""

    def test_it_equals_shared_on_windows(self):
        """`shared` already carries the hostname there, so the two coincide -- and must,
        or logs would stop landing beside the products they belong with."""
        filer = _filer_as("Windows", "mast00")

        assert filer.machine.root == filer.shared.root

    @pytest.mark.skipif(platform.system() != "Windows", reason="needs the real Z: mapping")
    def test_the_live_value_names_this_machine(self):
        if not is_windows_drive_mapped("Z:"):
            pytest.skip("Z: is not mapped, so the root falls back to the local disk")

        root = Filer().machine_log_root().replace("\\", "/")

        assert socket.gethostname().lower() in root.lower()
        assert root.startswith("Z:/MAST/")


class TestTheFallback:
    """Reachability is probed on the share, not on the returned path."""

    @pytest.mark.skipif(platform.system() != "Windows", reason="the fallback only exists on Windows")
    def test_it_falls_back_to_the_local_disk_when_the_share_is_gone(self):
        filer = Filer()
        with patch("common.filer.is_accessible", return_value=False):
            assert filer.machine_log_root() == filer.local.root

    def test_a_machine_folder_that_does_not_exist_yet_is_still_used(self):
        """`is_accessible` answers "is a directory there". Probing the target itself would
        send a newly-provisioned machine's logs to the local disk forever, since its folder
        is created by the first write. The probe is on the share for exactly that reason."""
        filer = _filer_as("Linux", "brand-new-host")
        seen: list[str] = []

        def _probe(path, timeout=2.0):
            seen.append(path)
            return True

        with patch("common.filer.is_accessible", _probe):
            root = filer.machine_log_root()

        assert root == "/Storage/mast-share/MAST/brand-new-host"
        assert seen == [filer.share_root.root], f"the probe must target the share, not the target path: {seen}"
