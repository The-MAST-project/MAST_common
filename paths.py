import datetime
import os
from pathlib import Path
from typing import Literal

from common.filer import Filer


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
    def make_daily_folder_name(root: str | None = None) -> str:
        if not root:
            ram = Filer().ram
            assert(ram)
            root = ram.root
        d = Path(root) / datetime.datetime.now().strftime("%Y-%m-%d")
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def make_exposures_folder(self, root: str | None = None) -> str:
        folder = Path(self.make_daily_folder_name(root=root)) / "Exposures"
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def make_autofocus_folder(self, root: str | None = None) -> str:
        autofocus_folder = Path(self.make_daily_folder_name(root=root)) / "Autofocus"
        folder = autofocus_folder / self.make_seq(str(autofocus_folder))
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def make_acquisition_folder(
        self, phase: str | None = None, tags: dict | None = None
    ) -> str:
        acquisitions_folder = Path(self.make_daily_folder_name()) / "Acquisitions"
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

    def make_guidings_folder(
        self, root: str | None = None, base_folder: str | None = None
    ) -> str:
        if base_folder is not None:
            guiding_folder = Path(base_folder) / "Guidings"
        else:
            if not root:
                ram = Filer().ram
                assert(ram)
                root = ram.root
            guiding_folder = Path(self.make_daily_folder_name(root=root)) / "Guidings"

        guiding_folder.mkdir(parents=True, exist_ok=True)
        return str(guiding_folder)

    def make_spirals_folder(
        self, root: str | None = None, base_folder: str | None = None
    ) -> str:
        if base_folder is not None:
            spirals_folder = Path(base_folder) / "Spirals"
        else:
            if not root:
                ram = Filer().ram
                assert(ram)
                root = ram.root
            spirals_folder = Path(self.make_daily_folder_name(root=root)) / "Spirals"

        spirals_folder = spirals_folder / PathMaker().make_seq(str(spirals_folder))
        spirals_folder.mkdir(parents=True, exist_ok=True)
        return str(spirals_folder)

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
    def make_tasks_folder() -> str:
        return str(Path(Filer().shared.root) / "tasks")

    @staticmethod
    def make_run_folder():
        daily_run_folder = PathMaker().make_daily_folder_name(
            root=os.path.join(Filer().shared.root, "runs")
        )
        return os.path.join(
            daily_run_folder, "run-" + PathMaker().make_seq(folder=daily_run_folder)
        )

    @staticmethod
    def make_spec_acquisitions_folder(spec_name: Literal["highspec", "deepspec"]):
        if spec_name not in ["highspec", "deepspec"]:
            raise Exception(
                f"bad {spec_name=}, should be one of ['highspec', 'deepspec']"
            )
        folder = PathMaker().make_daily_folder_name(os.path.join(Filer().shared.root))
        folder = os.path.join(folder, spec_name)
        folder = os.path.join(
            folder, "acquisition-" + PathMaker().make_seq(folder, None)
        )
        os.makedirs(folder, exist_ok=True)
        return folder


if __name__ == "__main__":
    print(PathMaker().make_spec_acquisitions_folder(spec_name="highspec"))
