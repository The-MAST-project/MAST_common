import os
from common.filer import Filer
import datetime
from typing import List, Literal


class PathMaker:

    @staticmethod
    def make_seq(folder: str, camera: str | None = None, start_with: int | None = None) -> str:
        """
        Creates a sequence number by maintaining a '.seq' file.
        The sequence may be camera specific or camera agnostic.
        :param folder: Where to maintain the '.seq' file
        :param camera: What camera is the sequence for
        :param start_with: Start the sequence at this number (default: 1)
        :return: The resulting sequence string
        """
        if camera:
            seq_file = os.path.join(folder, f'.{camera}.seq.txt')
        else:
            seq_file = os.path.join(folder, '.seq.txt')

        os.makedirs(os.path.dirname(seq_file), exist_ok=True)
        if os.path.exists(seq_file):
            with open(seq_file) as f:
                seq = int(f.readline())
        else:
            seq = start_with if start_with is not None else 0
        seq += 1
        with open(seq_file, 'w') as file:
            file.write(f'{seq}\n')

        return f"{seq:04d}"

    @staticmethod
    def make_daily_folder_name(root: str | None = None):
        if not root:
            root = Filer().ram.root
        d = os.path.join(root, datetime.datetime.now().strftime('%Y-%m-%d'))
        os.makedirs(d, exist_ok=True)
        return d

    def make_exposures_folder(self, root: str | None = None) -> str:
        folder = os.path.join(self.make_daily_folder_name(root=root), 'Exposures')
        os.makedirs(folder, exist_ok=True)
        return folder

    def make_autofocus_folder(self, root: str | None = None) -> str:
        autofocus_folder = os.path.join(self.make_daily_folder_name(root=root), 'Autofocus')
        ret: str = os.path.join(autofocus_folder, self.make_seq(autofocus_folder))
        os.makedirs(ret, exist_ok=True)
        return ret

    def make_acquisition_folder(self, phase: str | None = None, tags: dict | None = None) -> str:
        acquisitions_folder = os.path.join(self.make_daily_folder_name(), 'Acquisitions')
        os.makedirs(acquisitions_folder, exist_ok=True)
        parts: List[str] = [
            f"seq={PathMaker.make_seq(folder=acquisitions_folder)}",
            f"time={self.current_utc()}"
        ]
        if tags:
            for k, v in tags.items():
                parts.append(f"{k}={v}" if v else "{k}")

        folder = os.path.join(acquisitions_folder, ','.join(parts))
        if phase:
            folder = os.path.join(folder, phase)
        os.makedirs(folder, exist_ok=True)
        return folder

    def make_guidings_folder(self, root: str | None = None, base_folder: str | None = None):
        if base_folder is not None:
            guiding_folder = os.path.join(base_folder, 'Guidings')
        else:
            if not root:
                root = Filer().ram.root
            guiding_folder = os.path.join(self.make_daily_folder_name(root=root), 'Guidings')

        os.makedirs(guiding_folder, exist_ok=True)
        return guiding_folder

    @staticmethod
    def current_utc():
        return datetime.datetime.now(datetime.timezone.utc).strftime('%H-%M-%S_%f')[:-3]

    def make_guiding_root_name(self, root: str | None = None):
        if not root:
            root = Filer().ram.root
        guiding_folder = os.path.join(self.make_daily_folder_name(root=root), 'Guidings')
        os.makedirs(guiding_folder, exist_ok=True)
        return os.path.join(guiding_folder, f'{PathMaker.make_seq(guiding_folder)}-{self.current_utc()}-')

    def make_acquisition_root_name(self, root: str | None = None):
        if not root:
            root = Filer().ram.root
        acquisition_folder = os.path.join(self.make_daily_folder_name(root=root), 'Acquisitions')
        os.makedirs(acquisition_folder, exist_ok=True)
        return os.path.join(acquisition_folder, f'{PathMaker.make_seq(acquisition_folder)}-{self.current_utc()}-')

    def make_logfile_name(self):
        daily_folder = os.path.join(self.make_daily_folder_name(root=Filer().shared.root))
        os.makedirs(daily_folder)
        return os.path.join(daily_folder, 'log.txt')

    @staticmethod
    def make_tasks_folder():
        return os.path.join(Filer().shared.root, 'tasks')

    @staticmethod
    def make_run_folder():
        daily_run_folder = PathMaker().make_daily_folder_name(root=os.path.join(Filer().shared.root, 'runs'))
        return os.path.join(daily_run_folder, 'seq=' + PathMaker().make_seq(folder=daily_run_folder))

    @staticmethod
    def make_spec_acquisitions_folder(spec_name: Literal['highspec', 'deepspec']):
        if not spec_name in ['highspec', 'deepspec']:
            raise Exception(f"bad {spec_name=}, should be one of ['highspec', 'deepspec']")
        folder = PathMaker().make_daily_folder_name(os.path.join(Filer().shared.root))
        folder = os.path.join(folder, spec_name)
        folder = os.path.join(folder, 'acquisition-' + PathMaker().make_seq(folder, None))
        os.makedirs(folder, exist_ok=True)
        return folder


if __name__ == '__main__':
    print(PathMaker().make_spec_acquisitions_folder(spec_name='highspec'))
