import datetime
import os
from pathlib import Path
from typing import Literal

from common.filer import Filer
from common.mast_logging import observing_night


class PathMaker:
    @staticmethod
    def make_seq(folder: str, start_with: int | None = None, dont_bump: bool = False) -> str:
        """
        Creates a sequence number by maintaining a '.seq' file.
        The sequence may be camera specific or camera agnostic.
        :param folder: Where to maintain the '.seq' file
        :param start_with: Start the sequence at this number (default: 1)
        :return: The resulting sequence string
        """
        seq_file = Path(folder) / "seq.txt"
        seq_file.parent.mkdir(parents=True, exist_ok=True)

        seq = int(seq_file.read_text()) if seq_file.exists() else start_with if start_with is not None else 0
        if not dont_bump:
            seq += 1
            seq_file.write_text(str(seq))

        return f"{seq:04d}"

    @staticmethod
    def make_observing_night_folder(root: str | None = None) -> str:
        """
        <root>/<observing-night>, created if missing.

        The label is an observing night (`common.mast_logging.observing_night`), not a
        calendar day: it turns at 12:00 UTC, so a night's products stay in one folder
        instead of splitting at 02:00-03:00 local, mid-run, and they carry the same
        name as that night's logs.

        This was `datetime.now()` -- naive LOCAL time and a calendar day -- which is
        why the frames from the night of 2026-08-04 are filed under 2026-08-05
        (MAST_common#28). Folders written before this change keep those names, and
        nothing distinguishes the two conventions, since the format is identical; no
        migration was attempted. Consumers discover the names by listing rather than
        computing them (`MAST_control`'s `DataServer.autofocus`, and the GUI through
        it), so they are unaffected by which convention a folder follows.
        """
        if not root:
            ram = Filer().ram
            assert ram
            root = ram.root
        d = Path(root) / observing_night(datetime.datetime.now(datetime.UTC))
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def make_exposures_folder(self, root: str | None = None) -> str:
        folder = Path(self.make_observing_night_folder(root=root)) / "Exposures"
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def make_autofocus_folder(self, root: str | None = None, subfolder: str | None = None) -> str:
        """
        <observing-night>[/<subfolder>]/Autofocus/<NNNN>

        `subfolder` names the instrument being focused, and only a caller that focuses more
        than one has anything to say: MAST_spec passes the spectrograph's name, MAST_unit
        omits it because there is one telescope focuser.

        It used to be hardcoded to "highspec" (e212729, 2026-06-08), which put the unit's
        TELESCOPE autofocus -- stepped focuser positions, a V-curve, a status sidecar --
        under the name of a spectrograph it has nothing to do with. The path is the only
        label these frames carry, so it sent anyone reading the tree looking for HighSpec
        data, hid the focus history from anyone after it, and would have collided with
        genuine HighSpec output in one directory. MAST_unit#87.

        Note the unit's flat layout is the restored one, not a new invention: the shared
        area carries <date>/Autofocus for sixteen nights between 2025-08-19 and 2026-05-12,
        against two under <date>/highspec/Autofocus.
        """
        # Path(self.make_daily_folder_name(root=root or Filer().shared.root))
        night = Path(self.make_observing_night_folder(root=root or Filer().ram.root))  # type: ignore
        autofocus_folder = (night / subfolder if subfolder else night) / "Autofocus"
        folder = autofocus_folder / self.make_seq(str(autofocus_folder))
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def make_acquisition_folder(self, phase: str | None = None, tags: dict | None = None) -> str:
        acquisitions_folder = Path(self.make_observing_night_folder()) / "Acquisitions"
        acquisitions_folder.mkdir(parents=True, exist_ok=True)
        parts: list[str] = [
            f"seq={PathMaker.make_seq(folder=str(acquisitions_folder))}",
            f"time={self.current_utc()}",
        ]
        if tags:
            for k, v in tags.items():
                parts.append(f"{k}={v}" if v else "{k}")

        folder = acquisitions_folder / ",".join(parts)
        if phase:
            folder = folder / phase
            folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def make_guidings_folder(self, root: str | None = None, base_folder: str | None = None) -> str:
        if base_folder is not None:
            guiding_folder = Path(base_folder) / "Guidings"
        else:
            if not root:
                ram = Filer().ram
                assert ram
                root = ram.root
            guiding_folder = Path(self.make_observing_night_folder(root=root)) / "Guidings"

        guiding_folder.mkdir(parents=True, exist_ok=True)
        return str(guiding_folder)

    def make_spirals_folder(self, root: str | None = None, base_folder: str | None = None) -> str:
        if base_folder is not None:
            spirals_folder = Path(base_folder) / "Spirals"
        else:
            if not root:
                ram = Filer().ram
                assert ram
                root = ram.root
            spirals_folder = Path(self.make_observing_night_folder(root=root)) / "Spirals"

        spirals_folder = spirals_folder / PathMaker().make_seq(str(spirals_folder))
        spirals_folder.mkdir(parents=True, exist_ok=True)
        return str(spirals_folder)

    def make_flux_metering_folder(self, root: str | None = None) -> str:
        """
        <ram>/<observing-night>/FluxMetering/<NNNN> -- one folder per run of
        `acquire_and_find_max_flux`.

        The same shape as `make_spirals_folder`, so the mover lands it at
        `<share>/<hostname>/<observing-night>/FluxMetering/<NNNN>`. The hostname is NOT
        joined here: `Filer().shared.root` already carries it on Windows, so a caller
        adding `socket.gethostname()` would produce `Z:/MAST/mast02/mast02/...`.

        An observing night, not a calendar date. A run takes 20-40 minutes and a session
        of them spans the small hours, so a calendar label would split one night's runs
        across two folders whose names give no hint they belong together.
        """
        if not root:
            ram = Filer().ram
            assert ram
            root = ram.root
        folder = Path(self.make_observing_night_folder(root=root)) / "FluxMetering"
        folder = folder / PathMaker().make_seq(str(folder))
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    @staticmethod
    def current_utc():
        return datetime.datetime.now(datetime.UTC).strftime("%H-%M-%S_%f")[:-3]

    # def make_guiding_root_name(self, root: str | None = None):
    #     if not root:
    #         root = Filer().ram.root
    #     guiding_folder = os.path.join(self.make_daily_folder_name(root=root), 'Guidings')
    #     os.makedirs(guiding_folder, exist_ok=True)
    #     return os.path.join(guiding_folder, f'{PathMaker.make_seq(guiding_folder)}-{self.current_utc()}-')

    # def make_acquisition_root_name(self, root: str | None = None):
    #     if not root:
    #         root = Filer().ram.root
    #     acquisition_folder = os.path.join(self.make_daily_folder_name(root=root), 'Acquisitions')
    #     os.makedirs(acquisition_folder, exist_ok=True)
    #     return os.path.join(acquisition_folder, f'{PathMaker.make_seq(acquisition_folder)}-{self.current_utc()}-')

    # def make_logfile_name(self):
    #     daily_folder = os.path.join(self.make_daily_folder_name(root=Filer().shared.root))
    #     os.makedirs(daily_folder)
    #     return os.path.join(daily_folder, 'log.txt')

    @staticmethod
    def make_plans_folder() -> str:
        return str(Path(Filer().shared.root) / "plans")

    @staticmethod
    def make_plans_files_folder() -> str:
        return str(Path(PathMaker.make_plans_folder()) / "files")

    @staticmethod
    def make_run_folder():
        nightly_run_folder = PathMaker().make_observing_night_folder(root=os.path.join(Filer().shared.root, "runs"))
        return os.path.join(nightly_run_folder, "run-" + PathMaker().make_seq(folder=nightly_run_folder))

    @staticmethod
    def make_spec_acquisitions_folder(spec_name: Literal["highspec", "deepspec"]):
        if spec_name not in ["highspec", "deepspec"]:
            raise Exception(f"bad {spec_name=}, should be one of ['highspec', 'deepspec']")
        location = Filer().ram
        assert location is not None
        folder = PathMaker().make_observing_night_folder(os.path.join(location.root))
        folder = os.path.join(folder, spec_name)
        folder = os.path.join(folder, "acquisition-" + PathMaker().make_seq(folder, None))
        os.makedirs(folder, exist_ok=True)
        return folder

    @staticmethod
    def make_spec_exposures_folder(spec_name: Literal["highspec", "deepspec"], band: str | None = None):
        if spec_name not in ["highspec", "deepspec"]:
            raise Exception(f"bad {spec_name=}, should be one of ['highspec', 'deepspec']")

        location = Filer().ram
        assert location is not None
        folder = PathMaker().make_observing_night_folder(os.path.join(location.root))
        folder = os.path.join(folder, spec_name, "Exposures")
        folder = os.path.join(folder, "seq=" + PathMaker().make_seq(folder, None))
        if band:
            folder = os.path.join(folder, band)
        os.makedirs(folder, exist_ok=True)
        return folder


if __name__ == "__main__":
    print(PathMaker().make_spec_acquisitions_folder(spec_name="highspec"))
