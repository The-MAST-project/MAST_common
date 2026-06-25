import platform
import socket

if platform.system() == "Windows":
    import win32api

import fnmatch
import os
import shutil
import time
from collections.abc import Callable
from contextlib import contextmanager
from enum import Enum, auto
from pathlib import Path
from threading import Lock, Thread

# Suffix for in-flight temp files. A product is written to "<name>.part" and
# atomically renamed to "<name>" only once the writer closes (see
# Filer.atomic_path), so a file under its final name is complete by construction.
# *.part files are never moved and never counted as products.
PART_SUFFIX = ".part"


def is_windows_drive_mapped(drive_letter):
    if platform.system() != "Windows":
        raise Exception("is_windows_drive_mapped: this is not a Windows platform")

    try:
        drives = win32api.GetLogicalDriveStrings()
        drives = drives.split("\000")[:-1]
        return drive_letter.upper() + "\\" in drives
    except Exception as e:
        print(f"is_windows_drive_mapped: An error occurred: {e}")
        return False


class FilerTop(Enum):
    Local = auto()
    Shared = auto()
    Ram = auto()


class Location:
    def __init__(self, drive: str | None, prefix: str):
        self.drive = drive
        self.prefix = prefix
        self.root = os.path.join(self.drive, self.prefix) if self.drive else self.prefix


class Filer:
    # Serializes all moves across every Filer instance, so concurrent
    # ram->shared movers never race each other on the same tree.
    _move_lock = Lock()

    def __init__(self, logger=None):
        sys = platform.system()
        if sys == "Windows":
            self.local = Location("C:/", "MAST/")
            self.shared = (
                Location("Z:/", f"MAST/{socket.gethostname()}/")
                if is_windows_drive_mapped("Z:")
                else Location("C:/", "MAST/")
            )
            self.ram = (
                Location("D:/", "MAST/")
                if is_windows_drive_mapped("D:")
                else Location("C:/", "MAST/")
            )
        elif sys == "Linux":
            self.local = Location(None, "/Storage/mast-share/MAST")
            self.shared = self.local
            self.ram = None

        self.tops = {
            FilerTop.Local: self.local,
            FilerTop.Shared: self.shared,
            FilerTop.Ram: self.ram,
        }
        self.logger = logger

    def info(self, msg):
        if self.logger:
            self.logger.info(msg)
        else:
            print(msg)

    def error(self, msg):
        if self.logger:
            self.logger.error(msg)
        else:
            print(msg)

    @staticmethod
    @contextmanager
    def atomic_path(final):
        """
        Context manager for write-safe product creation. Yields a temporary
        '<final>.part' to write into, then atomically publishes it as `final`
        (os.replace) once the writer closes. On any error the temp is removed, so
        a partially written file never appears under the final name. Because the
        final name only ever appears via this atomic rename, "it exists" means
        "it is complete" -- which is what makes the move write-safe without
        guessing from file size.

        Usage::

            with filer.atomic_path(path) as tmp:
                hdu_list.writeto(tmp)   # or open(tmp, "w") / savefig(tmp) / ...
            # `path` now exists and is complete
        """
        final = Path(final)
        part = final.with_name(final.name + PART_SUFFIX)
        final.parent.mkdir(parents=True, exist_ok=True)
        try:
            yield str(part)
            os.replace(part, final)  # atomic within the volume
        except BaseException:
            try:
                part.unlink()
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _wait_for_path(path: Path, timeout: float = 30.0, poll: float = 0.5) -> bool:
        """
        Wait (bounded) for `path` to exist. Existence is a sound completion
        signal because products are published atomically (see atomic_path), so
        -- unlike size polling -- this never mistakes a mid-write file for a
        finished one; it only absorbs the small ordering slack between a producer
        publishing a file and the mover being asked to move it. Returns False if
        the path never appears within `timeout`.
        """
        deadline = time.monotonic() + timeout
        while True:
            if path.exists():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll)

    def _publish(self, src: Path, dst: Path):
        """
        Move one file/symlink to `dst` atomically: stage to '<dst>.part' (a
        cross-volume copy when ram and shared differ) then os.replace onto `dst`
        (atomic within the destination volume), so a reader on the destination
        never observes a partial file under the final name.
        """
        dst.parent.mkdir(parents=True, exist_ok=True)
        staged = dst.with_name(dst.name + PART_SUFFIX)
        if staged.exists():
            staged.unlink()
        shutil.move(str(src), str(staged))
        os.replace(staged, dst)

    def move(self, src, dst) -> bool:
        """
        Moves a source path (file or folder) to a destination, publishing it
        atomically so neither the mover nor a destination reader ever sees a
        partial file. In-flight '*.part' temps are skipped. Logs every
        successful move (the audit trail) and returns True on success.

        :param src: Source
        :param dst: Destination
        :return: True if the move succeeded
        """
        op = "move"
        if not isinstance(src, Path):
            src = Path(src)
        if not isinstance(dst, Path):
            dst = Path(dst)

        if src.name.endswith(PART_SUFFIX):
            return False  # never move an in-flight temp

        # Bounded existence wait (outside the lock so it does not stall other
        # movers); only the move itself is serialized.
        if not self._wait_for_path(src):
            self.error(f"{op}: path does not exist, ignoring: '{src.as_posix()}'")
            return False

        with Filer._move_lock:
            try:
                if src.is_dir():
                    # Publish each finished file; skip any in-flight '*.part' so an
                    # end-of-activity folder move never grabs a file mid-write.
                    for child in sorted(src.rglob("*")):
                        if child.is_file() and not child.name.endswith(PART_SUFFIX):
                            self._publish(child, dst / child.relative_to(src))
                    shutil.rmtree(src, ignore_errors=True)
                elif src.is_file() or src.is_symlink():
                    self._publish(src, dst)
                else:
                    self.error(f"{op}: not a file, folder or symlink, ignoring: '{src.as_posix()}'")
                    return False
                self.info(f"{op}: moved '{src.as_posix()}' -> '{dst.as_posix()}'")
                return True
            except Exception as e:
                self.error(f"failed to move '{src.as_posix()} to '{dst.as_posix()}' (exception: {e})")
                return False

    def change_top_to(self, top: FilerTop, path: str):
        for t in self.tops:
            if path.startswith(self.tops[t].root):
                return path.replace(self.tops[t].root, self.tops[top].root)

    def move_to(self, dst_top: FilerTop, src_paths: str | list[str]):
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
                continue  # it's already on the destination root
            for top in self.tops:
                if src_path.startswith(self.tops[top].root):
                    src_root = self.tops[top].root
                    break
            if not src_root:
                continue
            self.move(src_path, src_path.replace(src_root, dst_root))

    def move_ram_to_shared(self, paths: str | list[str]):
        """
        Moves stuff from the 'ram' storage to the 'shared' storage, in the
        background. The path hierarchy is preserved; only the 'root' changes
        from the 'ram' root to the 'shared' root.

        All paths in one call are handled by a single worker (sequentially, and
        serialized against every other mover), so moves never race each other or
        the producers still writing the files. A reconciliation line is logged
        for multi-file calls, so a dropped item is detected, not silently lost.

        :param paths: Can be one of:
                    - A file name: it will be moved
                    - A list of files: they will be moved
                    - A folder name: the whole folder will be recursively moved
        :return:
        """
        if isinstance(paths, str):
            paths = [paths]

        assert self.ram is not None
        items = [Path(p).as_posix() for p in paths]

        def _mover():
            moved = sum(
                self.move(src, src.replace(self.ram.root, self.shared.root))
                for src in items
            )
            if len(items) > 1:
                self.info(f"ram->shared: {moved}/{len(items)} item(s) reached '{self.shared.root}'")

        Thread(name="ram-to-shared-mover", target=_mover).start()

    def clean_ram_tmp(self):
        """
        Removes plate-solve scratch directories (`<ram>/tmp/tmp_*`) left on the
        RAM disk by solve-field. Safe to call between solves: it deletes only the
        solver's own temp dirs, never products.
        """
        if not self.ram:
            return
        for d in (Path(self.ram.root) / "tmp").glob("tmp_*"):
            try:
                shutil.rmtree(d, ignore_errors=True)
                self.info(f"clean_ram_tmp: removed '{d.as_posix()}'")
            except Exception as e:
                self.error(f"clean_ram_tmp: failed on '{d.as_posix()}' ({e})")

    def find_latest(
        self,
        root: str,
        name: str | None = None,
        pattern=None,
        qualifier: Callable = os.path.isfile,
    ) -> str | None:
        matches = []
        roots = [self.shared.root, self.local.root]
        if self.ram:
            roots.append(self.ram.root)

        if root not in roots:
            raise Exception(f"root must be one of {','.join(roots)}")

        # Walk through the directory and find matching files
        for top, folders, files in os.walk(root):
            # If name is provided, look for an exact match
            if name and ((qualifier is os.path.isfile and name in files) \
                         or (qualifier is os.path.isdir and name in folders)):
                    matches.append(os.path.join(top, name))

            # If pattern is provided, look for matching files using it
            if pattern:
                where = files if qualifier is os.path.isfile else folders
                for filename in fnmatch.filter(where, pattern):
                    matches.append(os.path.join(top, filename))

        # Sort the matched files by creation date
        matches_sorted = sorted(matches, key=os.path.getctime, reverse=True)

        return matches_sorted[0] if (matches_sorted and len(matches_sorted) > 0) else None
