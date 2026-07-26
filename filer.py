import platform
import socket

if platform.system() == "Windows":
    import win32api

import fnmatch
import os
import shutil
import time
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path
from threading import Condition, Lock, Thread


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


def is_accessible(path: str, timeout: float = 2.0) -> bool:
    """True if `path` is a reachable directory within `timeout` seconds.
    Guards against a hung SMB/NFS mount blocking the caller."""
    result: dict[str, bool] = {}

    def _check():
        try:
            result["ok"] = os.path.isdir(path)
        except OSError:
            result["ok"] = False

    t = Thread(target=_check, name="filer-accessible-probe", daemon=True)
    t.start()
    t.join(timeout)
    return result.get("ok", False)


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
    # Deferred ram->shared moves, retried by a single background sweeper when the shared
    # area is unreachable. Class-level so all Filer instances share one queue/sweeper.
    _pending: dict[str, str] = {}  # src -> dst
    _pending_lock = Lock()
    _sweeper_thread = None
    _sweeper_lock = Lock()
    _SWEEP_INTERVAL_SEC = 30

    def __init__(self, logger=None):
        sys = platform.system()
        if sys == "Windows":
            self.local = Location("C:/", "MAST/")
            self.shared = (
                Location("Z:/", f"MAST/{socket.gethostname()}/")
                if is_windows_drive_mapped("Z:")
                else Location("C:/", "MAST/")
            )
            self.ram = Location("D:/", "MAST/") if is_windows_drive_mapped("D:") else Location("C:/", "MAST/")
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

    def accessible_shared_root(self) -> str:
        """`shared.root` if the share is reachable, else `local.root`.
        On Linux the two are identical, so this is a harmless no-op there."""
        return self.shared.root if is_accessible(self.shared.root) else self.local.root

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

    def move(self, src, dst):
        """
        Moves a source path (either file or folder) to a destination path

        :param src: Source
        :param dst: Destination
        :return:
        """
        op = "move"
        if not isinstance(src, Path):
            src = Path(src)
        if not isinstance(dst, Path):
            dst = Path(dst)

        guardian = MoveGuardian()
        if guardian.is_protected(src):
            self.info(f"{op}: waiting for protected source(s) under '{src.as_posix()}'")

        # Claim the source for the whole move: blocks until no writer is protecting it
        # (or anything above/below it), and blocks any new overlapping protection until
        # the move completes. Claiming and moving are atomic w.r.t. protect(), which
        # closes the check->move race.
        with guardian.moving(src):
            try:
                if not src.exists():
                    self.error(f"{op}: path does not exist, ignoring: '{src.as_posix()}'")
                    return
                if src.is_file() or src.is_dir() or src.is_symlink():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(src, dst)
                else:
                    self.error(f"{op}: not a file, folder or symlink, ignoring: '{src.as_posix()}'")
                    return

                # self.info(f"moved '{src.as_posix()}' to '{dst.as_posix()}'")
            except Exception as e:
                self.error(f"failed to move '{src.as_posix()} to '{dst.as_posix()}' (exception: {e})")

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

        assert self.ram is not None
        for file in paths:
            src = Path(file).as_posix()
            dst = Path(str(src).replace(self.ram.root, self.shared.root))
            if is_accessible(self.shared.root):
                Thread(
                    name="ram-to-shared-mover",
                    target=self._move_ram_file,
                    args=[str(src), str(dst)],
                ).start()
            else:
                # Don't spin up a thread that would only block on / fail against a dead
                # share: defer the file (it stays safely in ram) for the sweeper to retry.
                self.info(f"move_ram_to_shared: shared area '{self.shared.root}' not accessible; deferring '{src}'")
                self._enqueue_pending(str(src), str(dst))
                self._ensure_sweeper()

    def _move_ram_file(self, src, dst):
        """Fast-path mover (one per file): move now, and if it didn't succeed (share went
        down mid-move, or any error), defer the file to the sweeper instead of losing it."""
        self.move(src, dst)
        if os.path.exists(src):  # move() swallows errors; a surviving src means it failed
            self._enqueue_pending(str(src), str(dst))
            self._ensure_sweeper()

    def _enqueue_pending(self, src, dst):
        with Filer._pending_lock:
            Filer._pending[str(src)] = str(dst)

    def _ensure_sweeper(self):
        """Start the single ram->shared sweeper if it isn't already running."""
        with Filer._sweeper_lock:
            if Filer._sweeper_thread is None:
                Filer._sweeper_thread = Thread(name="ram-to-shared-sweeper", target=self._sweep_loop, daemon=True)
                Filer._sweeper_thread.start()

    def _drain_pending(self):
        """Attempt every queued move once; drop entries that succeed or whose source is
        gone, and skip (retry next cycle) sources still being written."""
        with Filer._pending_lock:
            items = list(Filer._pending.items())
        for src, dst in items:
            if not os.path.exists(src):  # already moved/cleaned elsewhere
                with Filer._pending_lock:
                    Filer._pending.pop(src, None)
                continue
            if MoveGuardian().is_protected(Path(src)):
                continue  # producer still writing; leave it for the next sweep
            self.move(src, dst)
            if not os.path.exists(src):  # succeeded
                with Filer._pending_lock:
                    Filer._pending.pop(src, None)

    def _sweep_loop(self):
        while True:
            if is_accessible(self.shared.root):
                self._drain_pending()
            # Exit (freeing the thread) only when nothing is left; re-checked atomically
            # with _ensure_sweeper so a concurrent enqueue can't be stranded.
            with Filer._sweeper_lock:
                with Filer._pending_lock:
                    if not Filer._pending:
                        Filer._sweeper_thread = None
                        return
            time.sleep(Filer._SWEEP_INTERVAL_SEC)

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
            if name and (
                (qualifier is os.path.isfile and name in files) or (qualifier is os.path.isdir and name in folders)
            ):
                matches.append(os.path.join(top, name))

            # If pattern is provided, look for matching files using it
            if pattern:
                where = files if qualifier is os.path.isfile else folders
                for filename in fnmatch.filter(where, pattern):
                    matches.append(os.path.join(top, filename))

        # Sort the matched files by creation date
        matches_sorted = sorted(matches, key=os.path.getctime, reverse=True)

        return matches_sorted[0] if (matches_sorted and len(matches_sorted) > 0) else None


def _flatten_paths(paths) -> list[str]:
    """Accept a single path, or any nesting of lists/tuples of paths, and return a flat list."""
    flat: list[str] = []
    for p in paths:
        if isinstance(p, (list, tuple)):
            flat.extend(_flatten_paths(p))
        else:
            flat.append(os.fspath(p))
    return flat


class MoveGuardian:
    """
    Process-wide guard that keeps ``Filer.move`` from moving files while they are still
    being produced.

    Producers wrap their writes::

        with MoveGuardian().protect(fitsfile):
            fits.writeto(fitsfile)  # protected for the duration of the block

    and ``Filer.move`` wraps the move itself in :meth:`moving`. The two are mutually
    exclusive over overlapping paths:

    * a move waits until nothing it touches is being written, then holds a claim so no
      new overlapping write can start until it finishes;
    * a ``protect`` waits until no move is touching its paths before it starts writing.

    "Overlapping" is bidirectional: protecting a file blocks moving its parent directory,
    AND protecting a directory blocks moving any file underneath it. Real paths
    (``os.path.realpath``) are used as keys; both registries are reference-counted so
    concurrent/nested claims of the same path are safe. All state changes happen under a
    single shared ``Condition`` and the lock is never held across the file I/O, so moves
    and writes of unrelated paths still run concurrently.

    Note: overlapping ``protect`` and ``moving`` claims must be taken on *different*
    threads (the norm -- the mover runs on a ``ram-to-shared-mover`` thread spawned by
    ``Filer.move_ram_to_shared``); taking both on one thread would self-deadlock.
    """

    _instance = None
    _protected: dict[str, int] = {}  # realpath -> refcount (files being written)
    _moving: dict[str, int] = {}  # realpath -> refcount (moves in progress)
    _condition = Condition()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def protect(self, *paths) -> "_Claim":
        """Context manager protecting the given path(s) for the duration of a ``with`` block.

        Accepts a single path, several positional paths, or list(s) of paths. Blocks on
        entry until no in-progress move overlaps any of the paths.
        """
        reals = [os.path.realpath(p) for p in _flatten_paths(paths)]
        return _Claim(reals, self._protected, self._moving)

    def moving(self, *paths) -> "_Claim":
        """Context manager claiming the given path(s) while ``Filer.move`` moves them.

        Blocks on entry until no protected write overlaps any of the paths.
        """
        reals = [os.path.realpath(p) for p in _flatten_paths(paths)]
        return _Claim(reals, self._moving, self._protected)

    def is_protected(self, path) -> bool:
        """True if ``path`` overlaps a path currently being written (equal, under, or above)."""
        with self._condition:
            return bool(self._conflicts(os.path.realpath(path), self._protected))

    @staticmethod
    def _conflicts(real: str, registry: dict[str, int]) -> list[str]:
        """Keys in ``registry`` overlapping ``real`` -- equal to it, under it, or an
        ancestor of it (caller holds the lock)."""
        return [p for p in registry if p == real or p.startswith(real + os.sep) or real.startswith(p + os.sep)]

    def wait_until_free(self, path, timeout: float | None = None) -> bool:
        """Block until ``path`` overlaps no path currently being written.

        Does not claim the path -- see :meth:`moving` for the claim ``Filer.move`` uses.

        :param timeout: optional overall timeout in seconds; ``None`` waits indefinitely.
        :return: True if the path is free, False if the timeout elapsed while still protected.
        """
        real = os.path.realpath(path)
        with self._condition:
            return self._condition.wait_for(lambda: not self._conflicts(real, self._protected), timeout=timeout)


class _Claim:
    """Context manager backing both :meth:`MoveGuardian.protect` and
    :meth:`MoveGuardian.moving`. On enter it blocks until none of its paths overlap the
    *other* registry, then reference-counts them into its *own* registry for the duration
    of the block; on exit it releases and wakes any waiters. ``protect`` claims
    ``_protected`` while waiting on ``_moving``; ``moving`` does the reverse -- so writes
    and moves of overlapping paths are mutually exclusive.
    """

    def __init__(self, reals: list[str], own: dict[str, int], other: dict[str, int]):
        self._reals = reals
        self._own = own
        self._other = other

    def __enter__(self) -> "_Claim":
        with MoveGuardian._condition:
            MoveGuardian._condition.wait_for(lambda: not any(MoveGuardian._conflicts(r, self._other) for r in self._reals))
            for real in self._reals:
                self._own[real] = self._own.get(real, 0) + 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        with MoveGuardian._condition:
            for real in self._reals:
                count = self._own.get(real, 0) - 1
                if count > 0:
                    self._own[real] = count
                else:
                    self._own.pop(real, None)
            MoveGuardian._condition.notify_all()
        return False
