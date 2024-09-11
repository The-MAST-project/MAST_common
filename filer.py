import socket
import win32api
import shutil
import os
import logging
# from common.mast_logging import init_log
from typing import List
from threading import Thread

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

    @staticmethod
    def copy(src: str, dst: str):
        try:
            shutil.copy2(src, dst)
            os.unlink(src)
            # logger.info(f"moved '{src}' to '{dst}'")
        except Exception as e:
            # logger.exception(f"failed to move '{src} to '{dst}'", exc_info=e)
            pass

    def move_ram_to_shared(self, files: str | List[str]):
        if isinstance(files, str):
            files = [files]

        for file in files:
            src = file
            dst = file.replace(self.ram.root, self.shared.root)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            Thread(name='ram-to-shared-mover', target=self.copy, args=[src, dst]).start()
