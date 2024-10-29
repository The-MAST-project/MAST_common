import socket
import win32api
import shutil
import os
import logging
# from common.mast_logging import init_log
from typing import List
from threading import Thread
import fnmatch
from enum import Enum, auto

# logger = logging.Logger('mast.unit.filer')
# init_log(logger)


def is_windows_drive_mapped(drive_letter):
    try:
        drives = win32api.GetLogicalDriveStrings()
        drives = drives.split('\000')[:-1]
        return drive_letter.upper() + "\\" in drives
    except Exception as e:
        print(f"An error occurred: {e}")
        return False


class Top(Enum):
    Local = auto()
    Shared = auto()
    Ram = auto()


class Location:
    def __init__(self, drive: str, prefix: str):
        self.drive = drive
        self.prefix = prefix
        self.root = self.drive + self.prefix


class Filer:
    def __init__(self):
        self.local = Location('C:\\', 'MAST\\')
        self.shared = Location('Z:\\', f"MAST\\{socket.gethostname()}\\") if is_windows_drive_mapped('Z:') \
            else Location('C:\\', 'MAST\\')
        self.ram = Location('D:\\', 'MAST\\') if is_windows_drive_mapped('D:') \
            else Location('C:\\', 'MAST\\')
        self.tops = {
            Top.Local: self.local,
            Top.Shared: self.shared,
            Top.Ram: self.ram,
        }

    @staticmethod
    def move(src: str, dst: str):
        """
        Moves a source path (either file or folder) to a destination path

        :param src: Source
        :param dst: Destination
        :return:
        """
        op = 'move'

        try:
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                os.unlink(src)
            elif os.path.isdir(src):
                shutil.copytree(src, dst)
                shutil.rmtree(src)
            else:
                raise Exception(f"{op}: '{src}' is neither a file nor a folder, don't know how to move")

            print(f"moved '{src}' to '{dst}'")
        except Exception as e:
            print(f"failed to move '{src} to '{dst}' (exception: {e})")
            pass

    def move_to(self, dst_top: Top, src_paths: str | List[str]):
        """
        Moves one or more source paths (files or folders) to a destination top,
         unless the source path already resides on the destination root.

        :param dst_top: The ID of the destination top
        :param src_paths: One or more names of files or folders
        :return:
        """
        if isinstance(src_paths, str):
            src_paths = [src_paths]

        dst_root = self.tops[dst_top].root
        for src_path in src_paths:
            src_root = None
            if src_path.startswith(dst_root):
                continue    # it's already on the destination root
            for top in self.tops.keys():
                if src_path.startswith(self.tops[top].root):
                    src_root = self.tops[top].root
                    break
            if not src_root:
                continue
            self.move(src_path, src_path.replace(src_root, dst_root))

    def move_ram_to_shared(self, paths: str | List[str]):
        """
        Moves stuff from the 'ram' storage to the 'shared' storage.
        The path name hierarchy is preserved, only the 'root' is changed from the 'ram' root to the 'shared' root

        :param paths: Can be one of:
                    - A file name: it will be moved
                    - A list of files: they will be moved
                    - A folder name: the whole folder will be recursively moved
        :return:
        """
        if isinstance(paths, str):
            paths = [paths]

        for file in paths:
            src = file
            dst = file.replace(self.ram.root, self.shared.root)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            Thread(name='ram-to-shared-mover', target=self.move, args=[src, dst]).start()

    def find_latest(self, root: str, name: str | None = None, pattern=None, qualifier: callable = os.path.isfile) -> str:
        matches = []
        roots = [self.ram.root, self.shared.root, self.local.root]

        if root not in roots:
            raise Exception(f"root must be one of {','.join(roots)}")

        # Walk through the directory and find matching files
        for top, folders, files in os.walk(root):
            # If name is provided, look for an exact match
            if name:
                if qualifier is os.path.isfile and name in files:
                    matches.append(os.path.join(top, name))
                elif qualifier is os.path.isdir and name in folders:
                    matches.append(os.path.join(top, name))

            # If pattern is provided, look for matching files using the pattern
            if pattern:
                where = files if qualifier is os.path.isfile else folders
                for filename in fnmatch.filter(where, pattern):
                    matches.append(os.path.join(top, filename))

        # Sort the matched files by creation date
        matches_sorted = sorted(matches, key=os.path.getctime, reverse=True)

        return matches_sorted[0] if len(matches_sorted) > 0 else None
