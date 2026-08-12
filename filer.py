import platform
import socket

if platform.system() == "Windows":
    import pywintypes
    import win32api
    import win32con
    import win32event
    import win32file
    import winerror
else:
    import fcntl

import contextlib
import fnmatch
import os
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, auto
from pathlib import Path
from threading import Condition, Lock, Thread
from typing import ClassVar


def is_windows_drive_mapped(drive_letter):
    if platform.system() != "Windows":
        raise Exception("is_windows_drive_mapped: this is not a Windows platform")

    try:
        drives = win32api.GetLogicalDriveStrings()
        drives = drives.split("\000")[:-1]
        return drive_letter.upper() + "\\" in drives
    except Exception as e:  # noqa: BLE001 -- a drive probe answers False; it must never fail its caller
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
    _pending: ClassVar[dict[str, str]] = {}  # src -> dst
    _pending_lock = Lock()
    _sweeper_thread = None
    _sweeper_lock = Lock()
    _SWEEP_INTERVAL_SEC = 30

    # Sources a mover is handling right now. `_pending` records intent before the move is
    # attempted (see move_ram_to_shared), so during a normal move the entry is queued *and*
    # in flight; without this the sweeper would try to move the same source in parallel.
    _in_flight: ClassVar[set[str]] = set()  # guarded by _pending_lock

    # One bounded pool for every ram->shared move, instead of a thread per file. A move is
    # short and IO-bound, so a small pool keeps the share busy; the point is the bound.
    # Thread-per-file had none, and `Thread.start()` raising under load or at interpreter
    # shutdown was how files went missing (#52).
    _mover_pool: ClassVar[ThreadPoolExecutor | None] = None
    _mover_pool_lock = Lock()
    _MOVER_WORKERS = 4

    # Held for the life of the process once a relocation sweep is taken, so a second
    # process cannot start one. Ephemeral: a named mutex on Windows, a lock file handle on
    # Linux -- both released by the kernel if this process dies.
    _sweep_guard: ClassVar[object | None] = None

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
            # The share itself, above every machine's product tree. NOT derived as the
            # parent of `shared.root`: that works on Windows only by coincidence, since
            # `shared.root` carries the hostname here and does not on Linux, so the same
            # expression would mean two different things depending on which role ran it.
            #
            # Deliberately without the drive-mapped fallback the others have. A caller
            # asking for the share wants the share; handing back `C:/MAST/` because Z:
            # is unmapped would put shared artifacts on a local disk under a name that
            # says otherwise -- the failure that lost frames on 2026-07-14.
            self.share_root = Location("Z:/", "MAST/")
        elif sys == "Linux":
            self.local = Location(None, "/Storage/mast-share/MAST")
            self.shared = self.local
            self.ram = None
            self.share_root = self.local

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
                if src.is_dir() and not src.is_symlink() and dst.is_dir():
                    # shutil.move onto an EXISTING directory nests instead of merging:
                    # it moves the source INTO the destination, giving `<dst>/<src.name>`.
                    # That is how mast00 grew `Acquisitions/Acquisitions` and six
                    # `spec/spec`, splitting one night's products across two levels.
                    # Harmless while this only ever moved files (their destination is a
                    # full path that does not exist yet); the product-relocation sweep is
                    # the first caller to pass folders, which is what exposed it.
                    self._merge_into(src, dst, op)
                elif src.is_file() or src.is_dir() or src.is_symlink():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(src, dst)
                else:
                    self.error(f"{op}: not a file, folder or symlink, ignoring: '{src.as_posix()}'")
                    return

                # self.info(f"moved '{src.as_posix()}' to '{dst.as_posix()}'")
            except (OSError, shutil.Error) as e:
                # shutil.move raises OSError for the filesystem cases (share gone,
                # permissions, disk full) and shutil.Error when a destination is in the
                # way. _move_ram_file re-queues on a surviving source, so a miss here is
                # retried rather than lost.
                self.error(f"failed to move '{src.as_posix()} to '{dst.as_posix()}' (exception: {e})")

    def _merge_into(self, src: Path, dst: Path, op: str = "move") -> None:
        """Move the CONTENTS of `src` into the existing directory `dst`, then drop `src`.

        Recurses where both sides have a folder of the same name, so two trees combine
        rather than one ending up inside the other.

        A name that exists on BOTH sides as anything but two folders is a collision
        between distinct products. Those are left where they are, and reported: the
        source stays on the ram area, where the next sweep retries it, which is
        recoverable. Overwriting would not be.
        """
        dst.mkdir(parents=True, exist_ok=True)
        for entry in sorted(src.iterdir()):
            target = dst / entry.name
            entry_is_dir = entry.is_dir() and not entry.is_symlink()
            if entry_is_dir and target.is_dir():
                self._merge_into(entry, target, op)
            elif target.exists():
                self.error(
                    f"{op}: '{target.as_posix()}' already exists and is not a folder on both sides; "
                    f"leaving '{entry.as_posix()}' in place rather than overwriting it"
                )
            else:
                shutil.move(entry, target)

        # Only succeeds once everything has gone; a collision above leaves it behind on
        # purpose, so the source survives for the next sweep instead of vanishing.
        try:
            src.rmdir()
        except OSError as e:
            self.error(f"{op}: '{src.as_posix()}' not empty after merging, left in place ({e})")

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
            # Two spellings, deliberately. The roots are stored posix-style ("D:/MAST/"),
            # so deriving the destination needs the posix form -- but the queue is keyed by
            # realpath, matching MoveGuardian's `_protected`/`_products` and the `os.sep`
            # comparison in `_is_under`. Keying it posix-style made `_folder_drained`'s
            # queued-move check silently never match on Windows, where the two differ.
            posix_src = str(Path(file).as_posix())
            dst = str(Path(posix_src.replace(self.ram.root, self.shared.root)))
            src = os.path.realpath(posix_src)

            # Write ahead: record the intent BEFORE trying to act on it, and clear it only
            # once the source is gone. Every way this can fail -- the share being down, the
            # move erroring, the mover never starting, the thread being killed mid-move --
            # then leaves the entry behind for the sweeper, instead of needing its own
            # rescue path. The previous order (act, then defer if it went wrong) could not
            # cover a failure to *start* the mover, which is #52.
            self._enqueue_pending(src, dst)

            if not is_accessible(self.shared.root):
                # Don't occupy a worker that would only block on / fail against a dead
                # share: leave it queued (the file stays safely in ram) for the sweeper.
                self.info(f"move_ram_to_shared: shared area '{self.shared.root}' not accessible; deferring '{src}'")
                self._ensure_sweeper()
                continue

            try:
                self._mark_in_flight(src)
                self._movers().submit(self._move_ram_file, src, dst)
            except RuntimeError as e:
                # Pool or interpreter shutting down -- `submit` refuses just as
                # `Thread.start()` did. Nothing to rescue: the entry is already queued.
                self._unmark_in_flight(src)
                self.info(f"move_ram_to_shared: could not start a mover for '{src}' ({e}); left queued")
                self._ensure_sweeper()

    def start_product_relocation_sweep(self, logger=None) -> bool:
        """Relocate whatever a previous run left on the ram area. Call once, at app startup.

        `_pending` lives in memory and its sweeper is a daemon thread, so nothing survives
        the process dying: a move that never completed is invisible to the next run. This is
        the only mechanism that recovers those, and on a unit it is the difference between
        an artifact reaching the shared area and being erased by the next reboot.

        Belongs in the *app lifespan* of a producer, not in a component's ``startup()``:
        that is an HTTP endpoint an operator can call again mid-night, and a sweep then
        would relocate folders that are live. At lifespan startup nothing is operational
        yet, so everything present is by definition a leftover.

        Returns False if another process already holds the sweep, or there is no ram area.
        """
        if self.ram is None or not os.path.isdir(self.ram.root):
            return False
        if not self._take_sweep_guard():
            self.info("product_relocation_sweeper: another process is sweeping; skipping")
            return False
        Thread(
            name="product-relocation-sweeper",
            target=self._relocate_products,
            args=(logger,),
            daemon=True,
        ).start()
        return True

    def _take_sweep_guard(self) -> bool:
        """One sweeper per machine, held by an ephemeral kernel object -- nothing on disk.

        Windows named objects are scoped per *session*, not per user, so a service in
        session 0 and an interactive process in session 1 would not see a bare name even
        running as the same account: hence ``Global\\``. Creating there needs
        SeCreateGlobalPrivilege, which a service token has; failing to take the guard means
        skipping the sweep, never proceeding unguarded.
        """
        if platform.system() == "Windows":
            try:
                Filer._sweep_guard = win32event.CreateMutex(None, False, r"Global\product_relocation_sweeper")
                return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS
            except Exception as e:  # noqa: BLE001 -- e.g. ACCESS_DENIED unelevated
                self.info(f"product_relocation_sweeper: could not take the guard ({e}); skipping")
                return False
        Filer._sweep_guard = _open_claim(_folder_lock_path(os.path.realpath(self.ram.root)))
        return _try_lock(Filer._sweep_guard, exclusive=True)

    def _relocate_products(self, logger=None) -> None:
        """Move every unclaimed folder holding files off the ram area, deepest first."""
        ram_root = os.path.realpath(self.ram.root)
        guardian = MoveGuardian()
        candidates = []
        for dirpath, _dirnames, filenames in os.walk(ram_root):
            real = os.path.realpath(dirpath)
            if real == ram_root:
                continue
            # A folder is a unit of relocation only if it directly holds files. Parents of
            # such folders are left alone, so a claimed leaf cannot be carried off inside
            # an unclaimed ancestor.
            if any(not (f.startswith(".") and f.endswith(".lock")) for f in filenames):
                candidates.append(real)

        moved = skipped = 0
        for folder in sorted(candidates, key=len, reverse=True):
            if not os.path.isdir(folder):
                continue  # already carried off inside something else
            if guardian.folder_is_claimed(folder):
                skipped += 1
                continue
            self.move_ram_to_shared(folder)
            moved += 1

        message = f"product_relocation_sweeper: relocated {moved} leftover folder(s), skipped {skipped} in use"
        (logger.info if logger else self.info)(message)

    def flush(self, timeout: float = 30.0) -> bool:
        """Wait for outstanding ram->shared moves. Call from the app lifespan's shutdown.

        Without this the movers are abandoned mid-flight at interpreter teardown -- which is
        how #52 was first seen, a solve's cleanup racing service shutdown. Draining here
        happens while the process is still healthy.

        Returns True if everything drained within the timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with Filer._pending_lock:
                if not Filer._pending:
                    return True
            time.sleep(0.2)
        with Filer._pending_lock:
            left = sorted(Filer._pending)
        self.error(f"flush: {len(left)} move(s) still outstanding after {timeout}s: {left}")
        return False

    def _movers(self) -> ThreadPoolExecutor:
        """The shared, bounded ram->shared mover pool, created on first use."""
        with Filer._mover_pool_lock:
            if Filer._mover_pool is None:
                Filer._mover_pool = ThreadPoolExecutor(
                    max_workers=Filer._MOVER_WORKERS, thread_name_prefix="ram-to-shared-mover"
                )
            return Filer._mover_pool

    def _move_ram_file(self, src, dst):
        """Move one queued file. The caller has already recorded it in `_pending`, so this
        only has to clear that record on success; every failure path leaves it for the
        sweeper by doing nothing."""
        try:
            self.move(src, dst)
        finally:
            self._unmark_in_flight(str(src))
        if os.path.exists(src):  # move() swallows errors; a surviving src means it failed
            self._ensure_sweeper()
        else:
            self._dequeue_pending(str(src))

    def _enqueue_pending(self, src, dst):
        with Filer._pending_lock:
            Filer._pending[str(src)] = str(dst)

    def _dequeue_pending(self, src):
        with Filer._pending_lock:
            Filer._pending.pop(str(src), None)

    def _mark_in_flight(self, src):
        with Filer._pending_lock:
            Filer._in_flight.add(str(src))

    def _unmark_in_flight(self, src):
        with Filer._pending_lock:
            Filer._in_flight.discard(str(src))

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
            items = [(src, dst) for src, dst in Filer._pending.items() if src not in Filer._in_flight]
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
            with Filer._sweeper_lock, Filer._pending_lock:
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


def _is_under(path: str, folder: str) -> bool:
    """True if ``path`` is ``folder`` itself or lies beneath it (both already realpaths)."""
    return path == folder or path.startswith(folder + os.sep)


def _flatten_paths(paths) -> list[str]:
    """Accept a single path, or any nesting of lists/tuples of paths, and return a flat list."""
    flat: list[str] = []
    for p in paths:
        if isinstance(p, (list, tuple)):
            flat.extend(_flatten_paths(p))
        else:
            flat.append(os.fspath(p))
    return flat


#
# Cross-process folder claims (MAST_common#56)
#
# MoveGuardian's registries are process-local, so they say nothing to a relocation sweep
# running in another process -- which could move a frame mid-write. A folder in use is
# therefore also claimed through a lock file *beside* it:
#
#     <daily>/acq-0001/         the folder
#     <daily>/.acq-0001.lock    the claim
#
# Producers take a shared lock, the sweeper a non-blocking exclusive one, so producers
# never serialise against each other while a sweep can still tell "someone is using this".
# The lock is a property of an open handle, so a crashed producer releases it with no
# cleanup to run; the file it leaves behind is simply re-acquirable.
#
# Beside rather than inside: an in-folder lock would be copied along with the folder by
# the cross-volume `shutil.move`, and would stop its own holder from moving the folder at
# all. Both verified on Windows, along with the shared/exclusive and crash-release
# behaviour -- see the measurements on #56.
#


def _folder_lock_path(real_folder: str) -> str:
    """The sibling claim file for a folder: ``<parent>/.<name>.lock``."""
    parent, name = os.path.split(real_folder.rstrip("\\/"))
    return os.path.join(parent, f".{name}.lock")


if platform.system() == "Windows":
    _LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
    _LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
    _LOCK_BYTES = 1  # one byte stands for the whole claim

    def _open_claim(path: str):
        handle = win32file.CreateFile(
            path,
            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
            None,
            win32con.OPEN_ALWAYS,
            0,
            None,
        )
        # A leading dot means nothing on NTFS; this is what actually keeps it out of
        # listings, and `dir /a` still shows it to anyone looking.
        with contextlib.suppress(Exception):
            win32api.SetFileAttributes(path, win32con.FILE_ATTRIBUTE_HIDDEN)
        return handle

    def _try_lock(handle, exclusive: bool) -> bool:
        flags = _LOCKFILE_FAIL_IMMEDIATELY | (_LOCKFILE_EXCLUSIVE_LOCK if exclusive else 0)
        try:
            # pywin32's LockFileEx takes five arguments -- the offset rides on the
            # OVERLAPPED, there is no separate reserved parameter.
            win32file.LockFileEx(handle, flags, _LOCK_BYTES, 0, pywintypes.OVERLAPPED())
            return True
        except pywintypes.error:
            return False

    def _close_claim(handle) -> None:
        with contextlib.suppress(Exception):
            handle.Close()

else:

    def _open_claim(path: str):
        return os.open(path, os.O_RDWR | os.O_CREAT, 0o644)

    def _try_lock(fd, exclusive: bool) -> bool:
        flags = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
            return True
        except OSError:
            return False

    def _close_claim(fd) -> None:
        with contextlib.suppress(OSError):
            os.close(fd)


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
    _protected: ClassVar[dict[str, int]] = {}  # realpath -> refcount (files being written)
    _moving: ClassVar[dict[str, int]] = {}  # realpath -> refcount (moves in progress)
    # Every path that has been protected at least once, i.e. every artifact a producer
    # declared worth keeping. Unlike _protected this is NOT cleared when the write ends --
    # it is the durable record release_folder() needs, since by the time a folder is
    # finished nothing is protected any more. Entries are dropped when their folder is
    # released. See release_folder() for the meaning this gives protect().
    _products: ClassVar[set[str]] = set()
    _condition = Condition()

    # Folders this process has claimed against other processes: realpath -> open handle on
    # the sibling lock file. Kept until release_folder() reaps the folder; see claim_folder.
    _folder_claims: ClassVar[dict[str, object]] = {}
    _claim_lock = Lock()

    # How long release_folder() keeps waiting for a folder to drain before giving up and
    # leaving it in place. Generous: a move deferred against an unreachable share is
    # retried by Filer's sweeper every 30s, and losing the folder is worse than keeping it.
    _RELEASE_TIMEOUT_SEC = 600.0
    _RELEASE_POLL_SEC = 2.0

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def protect(self, *paths) -> "_Claim":
        """Context manager protecting the given path(s) for the duration of a ``with`` block.

        Accepts a single path, several positional paths, or list(s) of paths. Blocks on
        entry until no in-progress move overlaps any of the paths.

        Protecting a path also records it as a *product* -- something worth keeping -- for
        release_folder(). Write scratch outside a protect() block if it should be discarded
        with its folder.
        """
        reals = [os.path.realpath(p) for p in _flatten_paths(paths)]
        for real in reals:
            self.claim_folder(os.path.dirname(real))
        return _Claim(reals, self._protected, self._moving, record_products=True)

    def claim_folder(self, folder) -> bool:
        """Advertise, to other processes, that this folder is in use. Idempotent.

        Held from the first protect() in the folder until release_folder() reaps it -- not
        just for the duration of a write. The gap between one exposure being moved and the
        next being written is precisely the state release_folder() cannot tell apart from a
        finished folder, so a claim that came and went with each write would leave it open.
        """
        real_folder = os.path.realpath(str(folder))
        with MoveGuardian._claim_lock:
            if real_folder in MoveGuardian._folder_claims:
                return True
            handle = _open_claim(_folder_lock_path(real_folder))
            if _try_lock(handle, exclusive=False):
                MoveGuardian._folder_claims[real_folder] = handle
                return True
            # Only an exclusive holder -- a sweep already relocating this folder -- can
            # refuse a shared lock. Rare, and not ours to resolve here.
            _close_claim(handle)
            return False

    def folder_is_claimed(self, folder) -> bool:
        """True if any process is using this folder. The sweeper's question."""
        real_folder = os.path.realpath(str(folder))
        with MoveGuardian._claim_lock:
            if real_folder in MoveGuardian._folder_claims:
                return True  # ours, and this process is the one asking
        lock_path = _folder_lock_path(real_folder)
        if not os.path.exists(lock_path):
            return False
        handle = _open_claim(lock_path)
        try:
            # A stale file from a crashed producer locks cleanly, which is how the design
            # avoids needing any cleanup protocol.
            return not _try_lock(handle, exclusive=True)
        finally:
            _close_claim(handle)

    def _release_claim(self, real_folder: str) -> None:
        """Drop our claim and remove the lock file, once the folder is finished with."""
        with MoveGuardian._claim_lock:
            handle = MoveGuardian._folder_claims.pop(real_folder, None)
        if handle is not None:
            _close_claim(handle)
        with contextlib.suppress(OSError):
            os.remove(_folder_lock_path(real_folder))

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

    def release_folder(self, folder, logger=None, timeout: float | None = None) -> None:
        """Declare that nothing more will be written under ``folder``, and reap it.

        The producer calls this once an acquisition (or any other job owning a ram-disk
        folder) has concluded, successfully or not. A reaper thread then waits until every
        *product* under the folder -- every path someone wrapped in :meth:`protect` -- has
        left the ram disk, and no write, move or deferred move is still outstanding
        underneath it. Only then is the folder removed, taking with it whatever was never
        protected: the exposure counter ``seq.txt``, and any scratch a producer chose not
        to declare.

        The close signal has to come from the producer: a folder mid-acquisition looks
        identical to a finished one, since between one exposure being moved and the next
        being written nothing is protected, nothing is moving and no product is left behind.

        Never blocks the caller, and never removes a folder it is unsure about -- on
        timeout the folder stays and the reason is logged.
        """
        Thread(
            name="ram-folder-reaper",
            target=self._reap_folder,
            args=(os.path.realpath(str(folder)), logger, timeout),
            daemon=True,
        ).start()

    def _reap_folder(self, real_folder: str, logger, timeout: float | None) -> None:
        def say(msg, error=False):
            if logger:
                (logger.error if error else logger.info)(msg)

        deadline = time.monotonic() + (self._RELEASE_TIMEOUT_SEC if timeout is None else timeout)
        try:
            while True:
                drained, why, blockers = self._folder_drained(real_folder)
                if drained:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    say(f"release_folder: giving up on '{real_folder}', keeping it -- {why}: {blockers}", error=True)
                    # Drop the claim even here. The folder stays, but nothing in this
                    # process will touch it again, so holding it would only stop a later
                    # relocation sweep from rescuing exactly the artifacts this line is
                    # complaining about.
                    self._release_claim(real_folder)
                    return
                # Never sleep past the deadline: with a long poll and a short timeout the
                # wait would otherwise be rounded up to a whole poll interval.
                time.sleep(min(self._RELEASE_POLL_SEC, remaining))

            if not os.path.isdir(real_folder):
                self.forget_products_under(real_folder)
                self._release_claim(real_folder)
                return

            # Everything worth keeping is out; whatever is left is being discarded on
            # purpose. Name it anyway -- an artifact nobody protected is indistinguishable
            # from a producer that forgot to, and this line is the only warning anyone will
            # get before it is deleted.
            casualties = [
                os.path.join(root, name)
                for root, _dirs, files in os.walk(real_folder)
                for name in files
                if name != "seq.txt"
            ]
            if casualties:
                say(
                    f"release_folder: '{real_folder}': discarding {len(casualties)} unprotected file(s): {casualties}",
                    error=True,
                )

            shutil.rmtree(real_folder, ignore_errors=True)
            self.forget_products_under(real_folder)
            # Released last: until the folder is actually gone, another process must still
            # see it as in use. Removes the sibling .lock too, so the pair disappears
            # together and nothing is left behind for the next run to wonder about.
            self._release_claim(real_folder)
            say(f"release_folder: removed '{real_folder}'")
        except Exception as e:  # noqa: BLE001 -- a reaper thread must not die silently
            say(f"release_folder: failed for '{real_folder}': {e}", error=True)

    def _folder_drained(self, real_folder: str) -> tuple[bool, str, list[str]]:
        """(True, '', []) when nothing under ``real_folder`` is still owed to the shared area.

        The third element is every path responsible, not just the first. Polling only needs
        the summary, but the give-up message needs the full list: that log line is the sole
        record of which artifacts were never evacuated, and the folder it names is about to
        be left on a ram disk that filled up once already.
        """
        with self._condition:
            unmoved = [p for p in self._products_under(real_folder) if os.path.exists(p)]
            if unmoved:
                return False, f"{len(unmoved)} product(s) not yet moved", sorted(unmoved)
            for registry, what in ((self._protected, "write"), (self._moving, "move")):
                busy = self._conflicts(real_folder, registry)
                if busy:
                    return False, f"{len(busy)} {what}(s) in progress", sorted(busy)
        with Filer._pending_lock:
            # Since move_ram_to_shared records intent up front, this covers moves that are
            # in flight as well as ones deferred against a dead share -- both mean the
            # folder still owes something to the shared area.
            queued = [s for s in Filer._pending if _is_under(s, real_folder)]
        if queued:
            return False, f"{len(queued)} queued move(s)", sorted(queued)
        return True, "", []

    def _products_under(self, real_folder: str) -> list[str]:
        """Products at or below ``real_folder`` (caller holds the lock)."""
        return [p for p in self._products if _is_under(p, real_folder)]

    def forget_products_under(self, folder) -> None:
        """Drop the product records for a folder, so the set does not grow for ever."""
        real = os.path.realpath(str(folder))
        with self._condition:
            self._products.difference_update(self._products_under(real))

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

    def __init__(self, reals: list[str], own: dict[str, int], other: dict[str, int], record_products: bool = False):
        self._reals = reals
        self._own = own
        self._other = other
        self._record_products = record_products

    def __enter__(self) -> "_Claim":
        with MoveGuardian._condition:
            MoveGuardian._condition.wait_for(lambda: not any(MoveGuardian._conflicts(r, self._other) for r in self._reals))
            for real in self._reals:
                self._own[real] = self._own.get(real, 0) + 1
            if self._record_products:
                # Durable, unlike _own: release_folder() needs to know what was declared
                # worth keeping long after the write has finished.
                MoveGuardian._products.update(self._reals)
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
