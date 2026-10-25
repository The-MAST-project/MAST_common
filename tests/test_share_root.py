"""`Filer.share_root` -- the share itself, above every machine's product tree.

Separate from `shared`, THIS machine's folder in it. Anything sited at the share level (the
vault, MAST_common#60; plans and runs) is anchored on `share_root`.

On Windows the share is reached by UNC, `//<controller_host>/mast-share/MAST/`, not through
a `Z:` mapping that had to exist in the right logon session at construction
(plans/filing-areas-design.md 9). The construction is exercised on every runner by
patching the platform, the hostname and the configured controller host.
"""

from __future__ import annotations

import platform
import socket
from pathlib import PurePath
from unittest.mock import patch

import pytest

from common.filer import Filer
from common.testing.filer import real_filer, real_filer_init


def _windows_filer(host: str | None, hostname: str = "mast00", d_mapped: bool = True) -> Filer:
    """A Filer built as on Windows, with ``host`` as the bootstrap config's controller_host
    (None: the config cannot be read)."""
    with (
        real_filer_init(),
        patch("platform.system", return_value="Windows"),
        patch("socket.gethostname", return_value=hostname),
        patch("common.filer.is_windows_drive_mapped", return_value=d_mapped),
        patch("common.filer._share_host", return_value=host),
        patch("common.filer.long_paths_enabled", return_value=True),
    ):
        return Filer()


class TestByUnc:
    def test_the_share_root_names_the_controller_and_the_share(self):
        filer = _windows_filer("mast-ns-control")
        assert filer.share_root.root == "//mast-ns-control/mast-share/MAST/"

    def test_this_machines_folder_is_inside_it(self):
        filer = _windows_filer("mast-ns-control", hostname="mast07")
        assert filer.shared.root == "//mast-ns-control/mast-share/MAST/mast07/"

    def test_the_bare_host_as_configured(self):
        """The name provisioning maps `Z:` with: an SMB session is keyed on the server name
        as written, so an FQDN would not reuse the mapping's session or credentials."""
        filer = _windows_filer("mast-ns-control")
        assert "//mast-ns-control/" in filer.share_root.root
        assert "." not in filer.share_root.root.split("/")[2]

    def test_no_drive_mapping_is_consulted_for_it(self):
        """`shared` used to be `C:/MAST/` -- for the life of the process -- whenever `Z:` was
        unmapped at construction; with `ram` also unmapped the two collapsed onto one
        folder, which deletes products (1.2). Unmapped drives now leave the share alone."""
        filer = _windows_filer("mast-ns-control", d_mapped=False)
        assert filer.shared.root == "//mast-ns-control/mast-share/MAST/mast00/"
        assert filer.shared.root != filer.ram.root

    def test_without_the_bootstrap_config_it_is_z(self):
        """A machine that cannot read its config runs no app, but `import common` must work."""
        filer = _windows_filer(None)
        assert filer.share_root.root == "Z:/MAST/"
        assert filer.shared.root == "Z:/MAST/mast00/"

    def test_the_share_is_probed_for_promotion_not_this_machines_folder(self):
        from common.filer import FilingArea

        filer = _windows_filer("mast-ns-control")
        assert filer._primary_probe(FilingArea.SHARE).root == "//mast-ns-control/mast-share/MAST/"


class TestFailuresOfTheWholeShare:
    @pytest.mark.parametrize("winerror", [53, 67, 1326, 1244, 1219])
    def test_a_server_or_logon_failure_demotes_it(self, winerror):
        filer = _windows_filer("mast-ns-control")
        exc = OSError(None, "x")
        exc.winerror = winerror  # type: ignore[attr-defined]
        from common.filer import FilingArea

        assert filer._area_level_failure(FilingArea.SHARE, exc) == "unreachable"

    def test_access_denied_on_one_path_does_not(self):
        from common.filer import FilingArea

        filer = _windows_filer("mast-ns-control")
        assert filer._area_level_failure(FilingArea.SHARE, PermissionError(13, "denied")) is None


class TestLongPaths:
    def test_warned_once_when_off(self, monkeypatch):
        warnings: list[str] = []
        monkeypatch.setattr(Filer, "_long_paths_warned", False)
        monkeypatch.setattr(Filer, "warning", lambda self, msg: warnings.append(msg))
        with patch("common.filer.long_paths_enabled", return_value=False):
            Filer._warn_without_long_paths(_windows_filer("mast-ns-control"))
            Filer._warn_without_long_paths(_windows_filer("mast-ns-control"))
        assert len(warnings) == 1 and "LongPathsEnabled" in warnings[0]

    @pytest.mark.parametrize("state", [True, None])
    def test_silent_when_on_or_unknown(self, monkeypatch, state):
        warnings: list[str] = []
        monkeypatch.setattr(Filer, "_long_paths_warned", False)
        monkeypatch.setattr(Filer, "warning", lambda self, msg: warnings.append(msg))
        with patch("common.filer.long_paths_enabled", return_value=state):
            Filer._warn_without_long_paths(_windows_filer("mast-ns-control"))
        assert warnings == []


class TestTheLiveLayout:
    @pytest.fixture
    def filer(self):
        # The real layout: the suite's Filer is pointed at temporary folders (common.testing.filer).
        return real_filer()

    def test_it_does_not_carry_the_hostname(self, filer):
        assert socket.gethostname().lower() not in filer.share_root.root.lower()

    def test_this_machines_folder_is_one_level_below_it(self, filer):
        assert PurePath(filer.shared.root).parent == PurePath(filer.share_root.root)

    def test_it_is_never_the_local_disk(self, filer):
        """Handing back a local path for "the share" is how frames were lost on 2026-07-14,
        and a vault read from there would be a different file than the one asked for."""
        assert filer.share_root.root.casefold() != filer.local.root.casefold() or platform.system() == "Linux"

    @pytest.mark.skipif(platform.system() != "Linux", reason="Linux layout")
    def test_linux_layout(self, filer):
        assert filer.share_root.root == "/Storage/mast-share/MAST/"


class TestWholeShareFolders:
    """Plans and runs belong to the share as a whole -- control's -- not to one machine."""

    def test_plans_are_under_the_share_root_not_this_machines_folder(self):
        from common.paths import PathMaker

        filer = Filer()  # the suite's layout: shared is a folder inside share_root
        plans = PurePath(PathMaker.make_plans_folder())
        assert plans.parent == PurePath(filer.share_root.root)

    def test_runs_are_under_the_share_root_not_this_machines_folder(self):
        from common.paths import PathMaker

        filer = Filer()
        run = PurePath(PathMaker.make_run_folder())
        assert PurePath(filer.share_root.root) / "runs" in run.parents
        assert PurePath(filer.shared.root) not in run.parents
