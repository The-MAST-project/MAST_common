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
import datetime
import errno
import fnmatch
import os
import re
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, auto
from pathlib import Path
from threading import Condition, Event, Lock, Thread
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


class FilingArea(Enum):
    """The three places a machine files things (plans/filing-areas-design.md 3).

    ``RAM`` is the RAM disk (``None`` on Linux); ``SHARE`` is THIS machine's folder on the
    share; ``LOCAL`` is the local disk.
    """

    RAM = auto()
    SHARE = auto()
    LOCAL = auto()


def _spelled(path) -> str:
    """``path`` forward-slashed, doubled slashes collapsed, no trailing slash; a UNC path keeps
    its leading ``//``. String work, as `canonical_root` is, so the two compare."""
    s = str(path).replace("\\", "/")
    lead = "//" if s.startswith("//") else "/" if s.startswith("/") else ""
    return lead + "/".join(part for part in s[len(lead) :].split("/") if part)


def canonical_root(root: str) -> str:
    """``root`` forward-slashed, with no doubled slash and exactly one trailing slash.

    String work only, never the file system -- no ``realpath``: it can hang on a dead
    redirector (and ``common.utils`` builds a ``Filer`` at import), it drops the trailing
    slash, and its answer for ``Z:`` changes with whether the share is up. A UNC root keeps
    its leading ``//``. plans/filing-areas-design.md 4.1.
    """
    s = root.replace("\\", "/")
    lead = "//" if s.startswith("//") else "/" if s.startswith("/") else ""
    rest = "/".join(part for part in s[len(lead) :].split("/") if part)
    if not rest:
        if lead == "/":
            return "/"
        raise ValueError(f"not a root: {root!r}")
    return f"{lead}{rest}/"


def _root_key(root: str) -> str:
    """What two roots are compared by: case-folded where the file system ignores case."""
    return root.casefold() if _CASE_INSENSITIVE else root


_CASE_INSENSITIVE = platform.system() == "Windows"

#: How long a readable() / writable() answer is reused. Moves and sweeps ask per file.
PROBE_CACHE_SECONDS = 5.0
#: The clock the probe cache reads; tests replace it.
_probe_clock = time.monotonic
_probe_lock = Lock()
#: (root key, "readable" | "writable") -> (when, answer). Module-level, not per Location:
#: `Filer()` is constructed everywhere and builds new Locations each time.
_probe_cache: dict[tuple[str, str], tuple[float, bool]] = {}
#: The probe running for a key, and the dict it will answer in. A probe that hangs is not
#: started again until it returns, so a dead share cannot pile up a thread per caller.
_probe_threads: dict[tuple[str, str], tuple[Thread, dict[str, bool]]] = {}
_probe_sequence = 0


class Location:
    """A filing area's root: one canonical, forward-slashed string (``canonical_root``).

    ``Location(root)``. The old ``Location(drive, prefix)`` form is still accepted, the two
    parts joined, until MAST_unit's tests have moved to the one-argument form.
    """

    def __init__(self, root: str | None, prefix: str | None = None):
        if prefix is not None:
            root = f"{root or ''}{prefix}"
        if not root:
            raise ValueError("a Location needs a root")
        self.root = canonical_root(root)

    def __eq__(self, other) -> bool:
        return isinstance(other, Location) and _root_key(self.root) == _root_key(other.root)

    def __hash__(self) -> int:
        return hash(_root_key(self.root))

    def __repr__(self) -> str:
        return f"Location({self.root!r})"

    def readable(self, timeout: float = 2.0, fresh: bool = False) -> bool:
        """The root is a directory, within ``timeout`` seconds. Cached PROBE_CACHE_SECONDS,
        unless ``fresh``."""
        return self._probe("readable", lambda: os.path.isdir(self.root), timeout, fresh)

    def writable(self, timeout: float = 2.0, fresh: bool = False) -> bool:
        """A folder can be created and removed in the root, within ``timeout`` seconds.

        Not ``os.access``: on Windows it reads only the read-only attribute, not ACLs or
        share permissions. The probe folder names host and PID, so concurrent processes do
        not collide. The root itself is never created. Cached PROBE_CACHE_SECONDS, unless
        ``fresh`` -- what a recovery check asks for.
        """
        global _probe_sequence
        with _probe_lock:
            _probe_sequence += 1
            name = f".mast-probe-{socket.gethostname()}-{os.getpid()}-{_probe_sequence}"

        def _create_and_remove() -> bool:
            probe = os.path.join(self.root, name)
            os.mkdir(probe)
            os.rmdir(probe)
            return True

        return self._probe("writable", _create_and_remove, timeout, fresh)

    def _probe(self, kind: str, check: Callable[[], bool], timeout: float, fresh: bool = False) -> bool:
        """One probe per key at a time; every caller waits on it, up to its own timeout.

        A caller arriving while a probe runs joins that probe rather than answering False:
        concurrent writers asking at once must all hear the real answer, not a refusal that
        later steps would read as the root failing. The probe caches its own answer when it
        finishes, so a late answer still counts. A timeout is cached as False for the
        window, so a hung share does not make every caller wait out the full timeout.
        """
        key = (_root_key(self.root), kind)
        with _probe_lock:
            cached = _probe_cache.get(key)
            if not fresh and cached is not None and _probe_clock() - cached[0] < PROBE_CACHE_SECONDS:
                return cached[1]
            running = _probe_threads.get(key)
            if running is not None and running[0].is_alive():
                thread, result = running
                start = False
            else:
                result: dict[str, bool] = {}

                def _run():
                    try:
                        ok = bool(check())
                    except OSError:
                        ok = False
                    result["ok"] = ok
                    with _probe_lock:
                        _probe_cache[key] = (_probe_clock(), ok)

                thread = Thread(target=_run, name=f"filer-{kind}-probe", daemon=True)
                _probe_threads[key] = (thread, result)
                start = True
        if start:
            thread.start()
        thread.join(timeout)
        with _probe_lock:
            if "ok" in result:  # under the lock: it may have come back just now
                return result["ok"]
            _probe_cache[key] = (_probe_clock(), False)  # timed out: refuse for the window
        return False


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

    # Collisions already reported, keyed by both sides' path, size and mtime, so the sweeper
    # retrying a blocked source every pass reports it once (#117), while a new file that
    # later collides under the same name is a new event and is reported again.
    _reported_collisions: ClassVar[set[tuple]] = set()
    _reported_collisions_lock = Lock()

    # Filing-area state (plans/filing-areas-design.md 5). Class-level, like `_pending`:
    # `Filer()` is constructed everywhere, and a demotion has to stick for every one of them.
    # An area absent from `_demoted` is on its primary; present, it is on its fallback, and
    # the value is why. Only RAM and SHARE are ever demoted, and only where they have a
    # fallback -- never on Linux.
    _demoted: ClassVar[dict[FilingArea, str]] = {}
    _state_lock = Lock()
    _monitor_thread: ClassVar[Thread | None] = None
    #: Wakes the monitor early -- `wake_monitor`. It otherwise waits out its interval.
    _monitor_wake = Event()
    #: How often a demoted area's primary is probed. Tests shorten it.
    MONITOR_INTERVAL_SECONDS: ClassVar[float] = 60.0
    #: Called with the area after it returns to its primary -- item 6's drain registers here.
    _promotion_hooks: ClassVar[list[Callable[[FilingArea], None]]] = []
    # Asking the supervisor to revive a RAM disk found gone (plans/filing-areas-design.md
    # 8.1). One request at a time; it re-asks while the answer is "in progress".
    _ramdisk_restart_thread: ClassVar[Thread | None] = None
    #: Held by the one drain of the fallbacks that may run at a time.
    _drain_lock = Lock()
    # The local disk's ceiling: at or above it, nothing more is written to the fallbacks.
    LOCAL_DISK_CEILING: ClassVar[float] = 0.90
    LOCAL_DISK_CACHE_SECONDS: ClassVar[float] = 5.0
    _local_usage: ClassVar[tuple[float, float] | None] = None  # (when, fraction used)
    _local_full: ClassVar[bool] = False
    #: time.time() of the RAM area's last switch between disk and fallback; 0 if never.
    _ram_switched: ClassVar[float] = 0.0
    #: Products already reported lost with the RAM disk, so each is reported once.
    _reported_lost: ClassVar[set[str]] = set()
    RAMDISK_RESTART_POLL_SECONDS: ClassVar[float] = 10.0
    RAMDISK_RESTART_DEADLINE_SECONDS: ClassVar[float] = 600.0
    #: Builds the supervisor client; tests replace it. None is `common.api.SupervisorApi`.
    _supervisor_factory: ClassVar[Callable[[], object] | None] = None

    def __init__(self, logger=None):
        sys = platform.system()
        if sys == "Windows":
            self.local = Location("C:/MAST/")
            self.shared = (
                Location(f"Z:/MAST/{socket.gethostname()}/") if is_windows_drive_mapped("Z:") else Location("C:/MAST/")
            )
            self.ram = Location("D:/MAST/") if is_windows_drive_mapped("D:") else Location("C:/MAST/")
            # The share itself, above every machine's product tree. NOT derived as the
            # parent of `shared.root`: that works on Windows only by coincidence, since
            # `shared.root` carries the hostname here and does not on Linux, so the same
            # expression would mean two different things depending on which role ran it.
            #
            # Deliberately without the drive-mapped fallback the others have. A caller
            # asking for the share wants the share; handing back `C:/MAST/` because Z:
            # is unmapped would put shared artifacts on a local disk under a name that
            # says otherwise -- the failure that lost frames on 2026-07-14.
            self.share_root = Location("Z:/MAST/")
            # Where RAM and SHARE go while their primary has failed. Two folders, never one
            # and never C:/MAST/ itself: a RAM product moved to the share fallback would
            # otherwise be moved onto itself (plans/filing-areas-design.md 1.2, 3).
            self.ram_fallback = Location("C:/MAST/ram-fallback/")
            self.share_fallback = Location("C:/MAST/share-fallback/")
        elif sys == "Linux":
            # Control serves the share from its own disk, so the share and the local disk
            # are one tree here.
            self.local = Location("/Storage/mast-share/MAST")
            self.share_root = self.local
            # THIS machine's folder in the share, as `shared` is on Windows. It used to be the
            # share root itself on Linux, with no hostname -- so anything per-machine derived
            # from it landed at the top of the share, beside every machine's folder instead
            # of inside one: mast-ns-control's logs did that for 89 nights
            # (2025-10-28..2026-08-06). Whole-share folders -- plans, runs -- are under
            # `share_root` (plans/filing-areas-design.md 3.1).
            self.shared = Location(f"/Storage/mast-share/MAST/{socket.gethostname().split('.')[0]}")
            self.ram = None
            # Control serves the share from its own disk: nothing to fall back to.
            self.ram_fallback = None
            self.share_fallback = None

        self.logger = logger

    def accessible_shared_root(self) -> str:
        """`shared.root` -- this machine's folder on the share -- if it is reachable, else
        `local.root`."""
        return self.shared.root if is_accessible(self.shared.root) else self.local.root

    def ensure_shared_root(self) -> bool:
        """True if this machine's product root on the share is usable, creating it if that
        is the only thing missing.

        `shared.root` is `Z:/MAST/<hostname>/` on Windows, and nothing ever created it:
        not this class, not provisioning. A machine whose directory is absent -- a new one,
        or one that was renamed -- therefore defers every product forever. `mast-ns-spec`
        spent from its rename until 2026-08-18 in exactly that state, with two months of
        exposures piling up on a volatile ram disk behind an INFO-level log line, and they
        all moved within seconds of the directory being created by hand.

        Creating it is only safe when the *share* is demonstrably mounted, which is why
        `share_root` is the thing probed rather than the parent of `shared.root`:

        * share up, our directory missing -> create it, and say so;
        * share down -> False, so the caller defers exactly as it did before. Creating a
          local `Z:/...` directory in that state is the failure that lost frames on
          2026-07-14;
        * nothing per-machine in the path (Z: unmapped, so `shared` is the local disk) ->
          never create. There is no per-machine component to add, and conjuring the
          share's own mount point would mask an unmounted share.
        """
        if is_accessible(self.shared.root):
            return True

        if self.share_root is None or self.shared.root in (self.share_root.root, self.local.root):
            return False

        if not is_accessible(self.share_root.root):
            return False

        try:
            os.makedirs(self.shared.root, exist_ok=True)
        except OSError as e:
            self.error(f"ensure_shared_root: could not create '{self.shared.root}' on the share: {e}")
            return False

        self.info(f"ensure_shared_root: created this machine's product root '{self.shared.root}'")
        return True

    def machine_log_root(self) -> str:
        """`<share>/<hostname>` -- `shared` -- if the share is reachable, else `local.root`.

        It read a separate `machine` attribute while `shared` was the share root itself on
        Linux: a Linux host writing under that put its night folders at the top of the
        share rather than in its own folder, which is how mast-ns-control ended up owning 89
        `<date>` folders beside `plans/` and `tasks/`. `shared` is per-machine everywhere
        now (plans/filing-areas-design.md 3.1).

        The reachability probe is on `share_root`, not on the returned path: this machine's
        folder legitimately does not exist yet the first time it logs, and `is_accessible`
        answers "is a directory there", so probing the target itself would send a new
        machine's logs to the local disk forever. The caller creates the directory.
        """
        return self.shared.root if is_accessible(self.share_root.root) else self.local.root

    def info(self, msg):
        if self.logger:
            self.logger.info(msg)
        else:
            print(msg)

    def warning(self, msg):
        if self.logger:
            self.logger.warning(msg)
        else:
            print(msg)

    def error(self, msg):
        if self.logger:
            self.logger.error(msg)
        else:
            print(msg)

    def move(self, src, dst) -> bool:
        """Move a file or folder to `dst`. True when nothing is left that a retry could change.

        Not "the source is gone": a folder that moved completely still survives holding its
        bookkeeping (see `_merge_into`), and the queue judged by existence never let it go
        (MAST_common#156). False is a failure or a collision, both of which a later sweep retries.
        """
        op = "move"
        if not isinstance(src, Path):
            src = Path(src)
        if not isinstance(dst, Path):
            dst = Path(dst)

        # Never onto itself, nor into or out of its own subtree. With neither D: nor Z:
        # mapped, `ram` and `shared` are both C:/MAST/, so moving a ram product to the
        # shared area is moving it onto itself: `_merge_into` then compared every file with
        # itself, found it "identical" and unlinked it -- verified on mast00 on 2026-10-13
        # (plans/filing-areas-design.md 1.2). True, not False: no retry can change it, and
        # False would requeue it for the sweeper every 30 s for the life of the process. The
        # files stay where they are, and the folder reaper, which waits for a product to be
        # gone from its source, keeps their folder.
        if _overlaps(src, dst):
            self.error(
                f"{op}: refusing to move '{src.as_posix()}' to '{dst.as_posix()}': "
                "they are the same path, or one is inside the other; leaving it in place"
            )
            return True

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
                    self.warning(f"{op}: path does not exist, ignoring: '{src.as_posix()}'")
                    return True
                if src.is_dir() and not src.is_symlink():
                    # shutil.move onto an EXISTING directory nests instead of merging:
                    # it moves the source INTO the destination, giving `<dst>/<src.name>`.
                    # That is how mast00 grew `Acquisitions/Acquisitions` and six
                    # `spec/spec`, splitting one night's products across two levels.
                    # Harmless while this only ever moved files (their destination is a
                    # full path that does not exist yet); the product-relocation sweep is
                    # the first caller to pass folders, which is what exposed it.
                    #
                    # Every directory goes this way, not only one whose destination already
                    # exists. `_merge_into` creates the destination anyway, and routing the
                    # other case through `shutil.move` carried the whole tree wholesale --
                    # sequence counter and lock claims included, which is exactly what must
                    # stay behind.
                    return not self._merge_into(src, dst, op)
                elif src.is_file() and not src.is_symlink():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    _move_file(src, dst)
                    return True
                elif src.is_dir() or src.is_symlink():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(src, dst)
                    return True
                else:
                    self.error(f"{op}: not a file, folder or symlink, ignoring: '{src.as_posix()}'")
                    return False

                # self.info(f"moved '{src.as_posix()}' to '{dst.as_posix()}'")
            except (OSError, shutil.Error) as e:
                # shutil.move raises OSError for the filesystem cases (share gone,
                # permissions, disk full) and shutil.Error when a destination is in the
                # way. _move_ram_file re-queues on a surviving source, so a miss here is
                # retried rather than lost.
                self.error(f"failed to move '{src.as_posix()} to '{dst.as_posix()}' (exception: {e})")
                return False

    def _merge_into(self, src: Path, dst: Path, op: str = "move") -> bool:
        """Move the CONTENTS of `src` into the existing directory `dst`, then drop `src`.

        Recurses where both sides have a folder of the same name, so two trees combine
        rather than one ending up inside the other.

        A name that exists on BOTH sides as anything but two folders is a collision. A
        byte-identical copy at the destination means the move already happened, so the
        source is dropped. Anything else is two distinct products: those are left where
        they are and reported once (see `_report_collision`). The source stays on the ram
        area, where the next sweep retries it, which is recoverable. Overwriting would not
        be.

        Bookkeeping (see `is_bookkeeping`) is never moved, and its presence is not a
        failure: the source folder is expected to survive holding it.

        Returns True if a collision was left behind anywhere under `src`.
        """
        dst.mkdir(parents=True, exist_ok=True)
        kept_bookkeeping = False
        kept_collision = False
        for entry in sorted(src.iterdir()):
            if is_bookkeeping(entry.name):
                kept_bookkeeping = True
                continue
            target = dst / entry.name
            entry_is_dir = entry.is_dir() and not entry.is_symlink()
            if entry_is_dir:
                # Into it, existing or not: file by file, so each file arrives whole (see
                # `_move_file`) and a nested folder's bookkeeping stays behind like the top
                # level's. `shutil.move` of a folder copied it under its final names.
                kept_collision |= self._merge_into(entry, target, op)
            elif target.exists():
                key = _collision_key(entry, target)
                with Filer._reported_collisions_lock:
                    known_to_differ = key in Filer._reported_collisions
                if known_to_differ:
                    kept_collision = True
                elif entry.is_file() and target.is_file() and _same_bytes(entry, target):
                    entry.unlink()
                    self.info(f"{op}: '{target.as_posix()}' already holds an identical copy; removed '{entry.as_posix()}'")
                else:
                    self._report_collision(key, entry, target, op)
                    kept_collision = True
            elif entry.is_file() and not entry.is_symlink():
                _move_file(entry, target)
            else:
                shutil.move(entry, target)

        # A subfolder still here kept bookkeeping or a reported collision of its own.
        kept_below = any(child.is_dir() and not child.is_symlink() for child in src.iterdir())
        if kept_bookkeeping or kept_collision or kept_below:
            # Expected, not a failure. Bookkeeping stays by design, and a collision has been
            # reported already; reporting the folder as well would fire on every sweep.
            return kept_collision

        # Only succeeds once everything has gone.
        try:
            src.rmdir()
        except OSError as e:
            self.error(f"{op}: '{src.as_posix()}' not empty after merging, left in place ({e})")
        return False

    def _report_collision(self, key: tuple, entry: Path, target: Path, op: str) -> None:
        """Report a real collision the first time it is seen; later sweeps retry it silently."""
        with Filer._reported_collisions_lock:
            if key in Filer._reported_collisions:
                return
            Filer._reported_collisions.add(key)
        self.error(
            f"{op}: '{target.as_posix()}' already exists and differs from '{entry.as_posix()}'; "
            f"leaving the source in place rather than overwriting it, and retrying quietly"
        )

    def root_of(self, area: FilingArea) -> Location | None:
        """The root of ``area`` on this machine; ``None`` for ``RAM`` on Linux."""
        return {FilingArea.RAM: self.ram, FilingArea.SHARE: self.shared, FilingArea.LOCAL: self.local}[area]

    def area_of(self, path) -> tuple[FilingArea, str]:
        """The filing area ``path`` lies under, and its path relative to that area's root.

        Pure string work: either separator, doubled slashes, any case on Windows. Only the
        LEADING root counts. When roots overlap -- collapsed onto one folder, or a fallback
        nested in the local disk -- the longest matching root wins. Raises ValueError when
        ``path`` is under none of them.
        """
        spelled = _spelled(path)
        best: tuple[FilingArea, Location] | None = None
        for area, location in self._every_root():
            if _root_key(spelled + "/").startswith(_root_key(location.root)) and (
                best is None or len(location.root) > len(best[1].root)
            ):
                best = (area, location)
        if best is None:
            raise ValueError(f"'{path}' is under none of this machine's filing areas")
        area, location = best
        return area, (spelled + "/")[len(location.root) :].rstrip("/")

    def to_top(self, area: FilingArea, path) -> str:
        """``path``, which lies under one filing area, re-rooted under ``area``'s root.

        Replaced `change_top_to` (removed): separator-agnostic, the leading root only, Linux-safe, and
        it raises -- ValueError -- when ``path`` is under no filing area, or ``area`` does not
        exist on this machine, instead of returning None. The result is forward-slashed. It
        converts; it never chooses: which area a product should go to is the mover's
        business (plans/filing-areas-design.md 3, item 3).
        """
        target = self.root_of(area)
        if target is None:
            raise ValueError(f"there is no {area.name} filing area on this machine")
        _, relative = self.area_of(path)
        return target.root + relative if relative else target.root

    def locate(self, path) -> str | None:
        """Where the product at ``path`` is now: on the share, else on the RAM disk; None if on
        neither. Its own area is not assumed -- a product moves after its path was handed out.

        Searched in the order of plans/filing-areas-design.md (item 3): the share, its
        fallback, the RAM disk, its fallback.
        """
        _, relative = self.area_of(path)
        for location in (
            self.root_of(FilingArea.SHARE),
            self.fallback_of(FilingArea.SHARE),
            self.root_of(FilingArea.RAM),
            self.fallback_of(FilingArea.RAM),
        ):
            if location is None:
                continue
            candidate = location.root + relative if relative else location.root
            if os.path.exists(candidate):
                return candidate
        return None

    def fallback_of(self, area: FilingArea) -> Location | None:
        """Where ``area`` goes while its primary has failed; None for LOCAL, and on Linux.

        `getattr`, not the attribute: Filers built by tests in other repos may predate it.
        """
        if area is FilingArea.RAM:
            return getattr(self, "ram_fallback", None)
        if area is FilingArea.SHARE:
            return getattr(self, "share_fallback", None)
        return None

    def _every_root(self):
        """(area, Location) for each primary and each fallback that exists here."""
        for area in FilingArea:
            for location in (self.root_of(area), self.fallback_of(area)):
                if location is not None:
                    yield area, location

    # --- filing-area state (plans/filing-areas-design.md 5, 5.1) ----------------------

    def current_root(self, area: FilingArea) -> str | None:
        """The root to create under for ``area`` right now: the fallback while demoted.

        Asked each time, never held: that is what makes a demotion stick. Cheap.
        """
        primary = self.root_of(area)
        if primary is None:
            return None
        with Filer._state_lock:
            demoted = area in Filer._demoted
        fallback = self.fallback_of(area)
        if not demoted or fallback is None:
            return primary.root
        # The fallback is on the local disk, and a full system disk is worse than lost
        # data: Windows itself degrades (6.4). None: nowhere to write until it frees.
        return None if self.local_disk_full() else fallback.root

    def local_disk_full(self) -> bool:
        """True while the local disk is at or above LOCAL_DISK_CEILING used.

        Then writes to either fallback are refused (plans/filing-areas-design.md 6.4):
        products stay where they are -- on the RAM disk, while it lives -- and are lost
        only if it fills or dies before the share returns. Draining is never refused: it
        frees the disk. One ERROR when the ceiling is first reached, one INFO when usage
        drops below it. The reading is cached LOCAL_DISK_CACHE_SECONDS; one that fails
        counts as not full, and is logged.
        """
        now = _probe_clock()
        with Filer._state_lock:
            cached = Filer._local_usage
        if cached is not None and now - cached[0] < Filer.LOCAL_DISK_CACHE_SECONDS:
            fraction = cached[1]
        else:
            try:
                usage = _disk_usage(self.local.root)
                fraction = usage.used / usage.total if usage.total else 0.0
            except OSError as e:
                self.error(
                    f"filing areas: cannot read the local disk's usage at '{self.local.root}' ({e}); taking it as not full"
                )
                fraction = 0.0
            with Filer._state_lock:
                Filer._local_usage = (now, fraction)
        full = fraction >= Filer.LOCAL_DISK_CEILING
        with Filer._state_lock:
            changed = full != Filer._local_full
            Filer._local_full = full
        if changed and full:
            self.error(
                f"filing areas: the local disk '{self.local.root}' is {fraction:.0%} used, at or above its "
                f"{Filer.LOCAL_DISK_CEILING:.0%} ceiling; refusing writes to the fallbacks until it frees"
            )
        elif changed:
            self.info(f"filing areas: the local disk '{self.local.root}' is {fraction:.0%} used, below its ceiling again")
        return full

    def report_lost(self, paths) -> list[str]:
        """Report the products in ``paths`` that went with the RAM disk; returns them.

        A product is lost only while RAM is demoted as GONE, when it is no longer where it
        was written and `locate` finds it nowhere -- not on the share, its fallback, or the
        RAM fallback. A product that simply moved is not lost, and neither is one protected
        but never written (an aborted frame) while the disk is fine. One ERROR for the lot,
        each product once (7.1): today these vanished from the queue, and from the folder
        reaper's count, without a word.
        """
        with Filer._state_lock:
            if Filer._demoted.get(FilingArea.RAM) != "gone":
                return []
        lost = []
        for path in paths:
            if os.path.exists(path):
                continue
            try:
                if self.locate(path) is not None:
                    continue
            except ValueError:
                continue  # not under any filing area: not ours to call lost
            with Filer._state_lock:
                if path in Filer._reported_lost:
                    continue
                Filer._reported_lost.add(path)
            lost.append(path)
        if lost:
            self.error(f"filing area RAM: {len(lost)} product(s) lost with the RAM disk: {sorted(lost)}")
        return lost

    def is_demoted(self, area: FilingArea) -> bool:
        with Filer._state_lock:
            return area in Filer._demoted

    def report_failure(self, area: FilingArea, path, exc: BaseException) -> str | None:
        """A client's write under ``area`` failed. A report, not a command: ``Filer`` decides.

        Returns the kind of failure that demoted the area -- ``"full"``, ``"gone"`` or
        ``"unreachable"`` -- or None when it was about the one path (access denied, a
        missing subfolder, a name in the way), which must not send every product elsewhere.
        An area with no fallback (LOCAL; anything on Linux) is never demoted.
        """
        if self.fallback_of(area) is None:
            return None
        kind = self._area_level_failure(area, exc)
        if kind is not None:
            self._demote(area, kind, f"{kind}: {path}: {exc}")
            if area is FilingArea.RAM and kind == "gone":
                # Their lock files went with the disk; the handles this process holds are
                # stale, and releasing them one by one would only fail (7.1).
                MoveGuardian().drop_claims_under(self.ram.root)  # type: ignore[union-attr]
                # Only for gone. A FULL disk is cured by moving products off, and a restart
                # wipes D: -- so it would destroy exactly the unmoved products a long share
                # outage leaves there. Path-level errors never get this far.
                self._request_ramdisk_restart()
        return kind

    def check_now(self, area: FilingArea) -> bool:
        """Probe ``area``'s primary at once rather than at the next minute; promote if it
        passes. True if ``area`` is on its primary afterwards."""
        if not self.is_demoted(area):
            return True
        if self._primary_writable(area, fresh=True):
            self._promote(area)
            return True
        return False

    def _primary_probe(self, area: FilingArea) -> Location | None:
        """What says an area's primary is back. For SHARE that is the share, not this
        machine's folder in it, which a new machine has not created yet (4.3)."""
        if area is FilingArea.SHARE:
            return getattr(self, "share_root", None) or self.root_of(area)
        return self.root_of(area)

    def _primary_writable(self, area: FilingArea, fresh: bool = False) -> bool:
        probe = self._primary_probe(area)
        return probe is not None and probe.writable(fresh=fresh)

    #: OSError.winerror values that mean the network path or device is gone.
    _AREA_LEVEL_WINERRORS: ClassVar[dict[int, str]] = {
        15: "gone",  # ERROR_INVALID_DRIVE
        21: "gone",  # ERROR_NOT_READY
        53: "unreachable",  # ERROR_BAD_NETPATH
        59: "unreachable",  # ERROR_UNEXP_NET_ERR
        64: "unreachable",  # ERROR_NETNAME_DELETED
        67: "unreachable",  # ERROR_BAD_NET_NAME
        1231: "unreachable",  # ERROR_NETWORK_UNREACHABLE
    }
    _AREA_LEVEL_ERRNOS: ClassVar[dict[int, str]] = {
        errno.ENOSPC: "full",
        errno.ENODEV: "gone",
        errno.ENXIO: "gone",
        errno.ETIMEDOUT: "unreachable",
        errno.EHOSTUNREACH: "unreachable",
        errno.ENETUNREACH: "unreachable",
        errno.ENETDOWN: "unreachable",
        errno.ESTALE: "unreachable",
    }

    def _area_level_failure(self, area: FilingArea, exc: BaseException) -> str | None:
        """``"full"`` / ``"gone"`` / ``"unreachable"`` for a failure of the area itself; None
        for one about the path. A "not found" is the area's only when its root is gone."""
        if not isinstance(exc, OSError):
            return None
        winerror = getattr(exc, "winerror", None)
        if winerror in Filer._AREA_LEVEL_WINERRORS:
            return Filer._AREA_LEVEL_WINERRORS[winerror]
        if exc.errno in Filer._AREA_LEVEL_ERRNOS:
            return Filer._AREA_LEVEL_ERRNOS[exc.errno]
        if isinstance(exc, TimeoutError):
            return "unreachable"
        if exc.errno == errno.ENOENT or winerror == 3:  # ERROR_PATH_NOT_FOUND
            probe = self._primary_probe(area)
            if probe is not None and not probe.readable(fresh=True):
                return "gone" if area is FilingArea.RAM else "unreachable"
        return None

    def _demote(self, area: FilingArea, kind: str, cause: str) -> None:
        with Filer._state_lock:
            if area in Filer._demoted:
                return
            Filer._demoted[area] = kind
            if area is FilingArea.RAM:
                Filer._ram_switched = time.time()
        fallback = self.fallback_of(area)
        self.warning(
            f"filing area {area.name}: primary '{self.root_of(area).root}' failed ({cause}); "  # type: ignore[union-attr]
            f"now on the fallback '{fallback.root if fallback else None}' until it is writable again"
        )
        self._ensure_monitor()

    def _promote(self, area: FilingArea) -> None:
        with Filer._state_lock:
            if Filer._demoted.pop(area, None) is None:
                return
            hooks = list(Filer._promotion_hooks)
            if area is FilingArea.RAM:
                Filer._ram_switched = time.time()
        self.info(f"filing area {area.name}: primary '{self.root_of(area).root}' is writable again; back on it")  # type: ignore[union-attr]
        for hook in hooks:
            try:
                hook(area)
            except Exception as e:  # noqa: BLE001 -- one hook must not stop the others, or the monitor
                self.error(f"filing area {area.name}: promotion hook {hook!r} failed: {e!r}")
        # What piled up on the fallbacks goes back at once, not at the next sweep (6.3). On
        # its own thread: the monitor must not wait on a drain of a night's products.
        Thread(name="fallback-drain", target=self.drain_fallbacks, daemon=True).start()

    def _ensure_monitor(self) -> None:
        with Filer._state_lock:
            if Filer._monitor_thread is not None and Filer._monitor_thread.is_alive():
                return
            Filer._monitor_thread = Thread(name="filing-area-monitor", target=Filer._monitor_loop, daemon=True)
            Filer._monitor_thread.start()

    @staticmethod
    def _monitor_loop() -> None:
        """Every MONITOR_INTERVAL_SECONDS, probe each demoted area's primary; promote those
        that pass. Exits when nothing is demoted, so a healthy process runs no thread."""
        while True:
            Filer._monitor_wake.wait(Filer.MONITOR_INTERVAL_SECONDS)
            Filer._monitor_wake.clear()
            with Filer._state_lock:
                demoted = list(Filer._demoted)
                if not demoted:
                    Filer._monitor_thread = None
                    return
            filer = Filer()
            for area in demoted:
                try:
                    if filer._primary_writable(area, fresh=True):
                        filer._promote(area)
                except Exception as e:  # noqa: BLE001 -- the monitor must survive any one probe
                    filer.error(f"filing-area monitor: probing {area.name}: {e!r}")

    def _request_ramdisk_restart(self) -> None:
        """Ask the supervisor to revive the RAM disk, from a background thread -- never from
        the failing write, which is already being retried on the fallback. A request already
        running is not duplicated."""
        with Filer._state_lock:
            running = Filer._ramdisk_restart_thread
            if running is not None and running.is_alive():
                return
            thread = Thread(name="ramdisk-restart-request", target=self._ramdisk_restart_loop, daemon=True)
            Filer._ramdisk_restart_thread = thread
        thread.start()

    def _ramdisk_restart_loop(self) -> None:
        """Ask, and act on the answer. RESTORED or HEALTHY: probe at once (`check_now`) --
        acting on this call's own answer, which is not the supervisor nudging the unit
        (5.1). GIVING_UP or no supervisor: say so once and stay on the fallback; the
        one-minute check finds the disk whenever it does come back. IN_PROGRESS: ask again,
        joining the restart that is running, up to RAMDISK_RESTART_DEADLINE_SECONDS."""
        from common.api import RamdiskRestartAnswer, SupervisorApi

        supervisor = (Filer._supervisor_factory or SupervisorApi)()
        deadline = time.monotonic() + Filer.RAMDISK_RESTART_DEADLINE_SECONDS
        self.info("filing area RAM: the RAM disk is gone; asking the supervisor to revive it")
        while True:
            try:
                answer, detail = supervisor.restart_ramdisk()  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001 -- a client bug must not kill this thread silently
                answer, detail = RamdiskRestartAnswer.NO_ANSWER, repr(e)
            if answer in (RamdiskRestartAnswer.RESTORED, RamdiskRestartAnswer.HEALTHY):
                self.info(f"filing area RAM: supervisor answered {answer.name} ({detail}); probing D: now")
                self.check_now(FilingArea.RAM)
                return
            if answer is RamdiskRestartAnswer.GIVING_UP:
                self.error(
                    f"filing area RAM: the supervisor has given up reviving the RAM disk ({detail}); staying on the fallback"
                )
                return
            if answer is RamdiskRestartAnswer.NO_ANSWER:
                self.warning(
                    f"filing area RAM: no answer to the RAM-disk restart request ({detail}); staying on the "
                    "fallback until the one-minute check finds the disk"
                )
                return
            if time.monotonic() >= deadline:
                self.warning(
                    f"filing area RAM: the RAM-disk restart is still in progress after "
                    f"{Filer.RAMDISK_RESTART_DEADLINE_SECONDS:.0f} s; leaving it to the one-minute check"
                )
                return
            time.sleep(Filer.RAMDISK_RESTART_POLL_SECONDS)

    @classmethod
    def ram_switched_at(cls) -> float:
        """When the RAM area last switched between its disk and its fallback (time.time()),
        or 0. A sequence counter older than this missed numbers given out on the other side
        (`PathMaker.make_seq`)."""
        with cls._state_lock:
            return cls._ram_switched

    @classmethod
    def wake_monitor(cls) -> None:
        """Run the monitor's next pass now rather than at the end of its interval. With
        nothing demoted, that pass is the one where it exits."""
        cls._monitor_wake.set()

    @classmethod
    def on_promotion(cls, hook: Callable[[FilingArea], None]) -> None:
        """Register ``hook(area)``, called after ``area`` returns to its primary."""
        with cls._state_lock:
            if hook not in cls._promotion_hooks:
                cls._promotion_hooks.append(hook)

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
            if not self._on_ram_area(posix_src):
                # Its destination would be itself, and `_move_ram_file` reads a surviving
                # source as a failed move: queued, it is never cleared and every `flush`
                # waits out its timeout (MAST_common#156).
                self.info(f"move_ram_to_shared: '{posix_src}' is not on the ram area; nothing to move")
                continue
            src = os.path.realpath(posix_src)
            # Informational: the destination is decided again when the move happens. Empty
            # while there is none yet (the share fallback refusing, C: at its ceiling).
            dst = self._share_destination(posix_src) or ""

            # Write ahead: record the intent BEFORE trying to act on it, and clear it only
            # once the source is gone. Every way this can fail -- the share being down, the
            # move erroring, the mover never starting, the thread being killed mid-move --
            # then leaves the entry behind for the sweeper, instead of needing its own
            # rescue path. The previous order (act, then defer if it went wrong) could not
            # cover a failure to *start* the mover, which is #52.
            self._enqueue_pending(src, dst)

            if not self._share_usable():
                # No share and no fallback for it (Linux; a Filer without one): don't
                # occupy a worker that would only block on / fail against a dead share --
                # leave it queued (the file stays in ram) for the sweeper.
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
        # With D: unmapped, `ram` falls back to `local`, C:/MAST/, and every folder there
        # holding files reads as a leftover product -- the RAM-disk image under Shared/, the
        # IERS and config-DB caches, Kepler/, Campaigns/, scripts. With the share mapped they
        # are carried onto it, the image needed to bring D: back included; with it unmapped
        # too, each is a move onto itself (plans/filing-areas-design.md 1.5). Nothing on the
        # local disk is a ram leftover, so there is nothing to sweep.
        for other in (self.local, self.shared):
            if other is not None and _canonical(self.ram.root) == _canonical(other.root):
                self.warning(
                    f"product_relocation_sweeper: the ram area '{self.ram.root}' is the same folder as "
                    f"'{other.root}' (the RAM disk is not mapped); not sweeping"
                )
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
        guardian = MoveGuardian()
        moved = skipped = 0
        for folder in _product_folders(self.ram.root):
            if not os.path.isdir(folder):
                continue  # already carried off inside something else
            if guardian.folder_is_claimed(folder):
                skipped += 1
                continue
            self.move_ram_to_shared(folder)
            moved += 1

        message = f"product_relocation_sweeper: relocated {moved} leftover folder(s), skipped {skipped} in use"
        (logger.info if logger else self.info)(message)
        # Whatever a previous run left on the fallbacks goes too (6.3).
        self.drain_fallbacks()

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

    def _move_ram_file(self, src, dst=None):
        """Move one queued file. The caller has already recorded it in `_pending`, so this
        only has to clear that record on success; every failure path leaves it for the
        sweeper by doing nothing.

        The destination is decided here, at the moment of the move (`_destination`), not
        taken from the queue: the share may have gone, or come back, since it was queued.
        """
        try:
            dst = self._destination(src)
            done = dst is not None and self.move(src, dst)
        finally:
            self._unmark_in_flight(str(src))
        if done:
            self._dequeue_pending(str(src))
        else:
            self._ensure_sweeper()

    def _on_ram_area(self, path) -> bool:
        """``path`` is under the RAM disk or the RAM fallback."""
        try:
            return self.area_of(path)[0] is FilingArea.RAM
        except ValueError:
            return False

    def _share_destination(self, src) -> str | None:
        """Where a product on the RAM area -- primary or fallback -- goes on the share now:
        the share's current root (its fallback while demoted) plus its path relative to the
        RAM area. None if ``src`` is not on the RAM area, or the share has nowhere to take
        it now (demoted, with C: at its ceiling)."""
        try:
            area, relative = self.area_of(src)
        except ValueError:
            return None
        if area is not FilingArea.RAM:
            return None
        root = self.current_root(FilingArea.SHARE)
        if root is None:
            return None
        return root + relative if relative else root

    def drain_fallbacks(self) -> tuple[int, int]:
        """Send what the fallbacks hold towards the share. Returns (from RAM, from share).

        * The RAM fallback goes the way any RAM product goes -- `move_ram_to_shared`, so to
          the share, or to its fallback while the share is down. Folders a producer has
          claimed are left for the next drain.
        * The share fallback goes to the share itself, and only while SHARE is on its
          primary and the share is reachable.

        One drain at a time: a request while one runs is skipped, since the running one
        will see the same folders. Empty folders it leaves behind in a fallback are removed.
        Runs on promotion, on each sweeper pass, and at start-up (6.3).
        """
        if not Filer._drain_lock.acquire(blocking=False):
            return 0, 0
        try:
            return self._drain_fallbacks()
        finally:
            Filer._drain_lock.release()

    def _drain_fallbacks(self) -> tuple[int, int]:
        guardian = MoveGuardian()
        from_ram = from_share = 0

        ram_fallback = self.fallback_of(FilingArea.RAM)
        if ram_fallback is not None and os.path.isdir(ram_fallback.root):
            for folder in _product_folders(ram_fallback.root):
                if os.path.isdir(folder) and not guardian.folder_is_claimed(folder):
                    self.move_ram_to_shared(folder)
                    from_ram += 1

        share_fallback = self.fallback_of(FilingArea.SHARE)
        if (
            share_fallback is not None
            and os.path.isdir(share_fallback.root)
            and not self.is_demoted(FilingArea.SHARE)
            and self.ensure_shared_root()
        ):
            for folder in _product_folders(share_fallback.root):
                if not os.path.isdir(folder):
                    continue
                destination = self.to_top(FilingArea.SHARE, folder)
                os.makedirs(os.path.dirname(destination.rstrip("/")), exist_ok=True)
                if self.move(folder, destination):
                    from_share += 1
            _remove_empty_folders(share_fallback.root)
            from_share += self._drain_legacy_night_folders()

        if from_ram or from_share:
            self.info(
                f"drain: sent {from_ram} folder(s) from the RAM fallback towards the share, "
                f"moved {from_share} from the share fallback to the share"
            )
        return from_ram, from_share

    def _drain_legacy_night_folders(self) -> int:
        """Move the ``C:/MAST/<night>/`` folders to this machine's folder on the share.

        What the construction-time fallback left whenever a process started with the share
        unreachable: its logs (and on mast00 one night's products) under ``C:/MAST/``
        itself (plans/filing-areas-design.md 6.3). Only top-level folders whose name is a
        night label -- never ``Shared/`` with the RAM-disk image, the caches, ``Kepler/``,
        ``Campaigns/`` or scripts. Not tonight's: a process that started with the share
        down may still be writing its log there, and an open file cannot be moved.

        Their logs are renamed ``mast-<role>-log.fallback.txt`` first: the share usually
        has its own ``mast-<role>-log.txt`` for the night, and the two differ, so moving
        them under one name would be a collision on every sweep (10).
        Only on a machine whose local disk is not the share -- never on control.
        """
        local, shared = self.local.root, self.root_of(FilingArea.SHARE)
        if shared is None or _overlaps(local, shared.root) or _overlaps(local, self.share_root.root):
            return 0
        from common.mast_logging import observing_night  # mast_logging imports this module

        tonight = observing_night(datetime.datetime.now(datetime.UTC))
        moved = 0
        for name in sorted(os.listdir(local)) if os.path.isdir(local) else []:
            folder = os.path.join(local, name)
            if name == tonight or not _is_night_label(name) or not os.path.isdir(folder):
                continue
            _rename_logs_as_fallback(folder)
            if self.move(folder, shared.root + name):
                moved += 1
        if moved:
            self.info(f"drain: moved {moved} legacy night folder(s) from '{local}' to '{shared.root}'")
        return moved

    def _share_usable(self) -> bool:
        """True if a product can be sent towards the share now: the share is reachable, or
        it is on its fallback. A share found unreachable is demoted on the spot when it has
        a fallback -- products go to `C:` now rather than wait on the RAM disk, which could
        fill (plans/filing-areas-design.md 6.1, "spill immediately"). False only when there
        is neither."""
        if self.is_demoted(FilingArea.SHARE):
            return True
        if self.ensure_shared_root():
            return True
        if self.fallback_of(FilingArea.SHARE) is None:
            return False
        self._demote(FilingArea.SHARE, "unreachable", f"unreachable: '{self.shared.root}' is not accessible")
        return True

    def _destination(self, src) -> str | None:
        """The destination of a RAM product, decided at the moment it moves, with its
        folder created -- the creation IS the reachability test: it is the real operation,
        so it cannot disagree with a probe (6.1). A failure that `report_failure` reads as
        the share's demotes it, and the folder is made under the share fallback instead.
        None: nowhere to put it yet -- it stays queued."""
        if not self._share_usable():
            return None
        dst = self._share_destination(Path(str(src)).as_posix())
        if dst is None:
            return None  # not a RAM product, or the share fallback refused (C: at its ceiling)
        try:
            os.makedirs(os.path.dirname(dst.rstrip("/")), exist_ok=True)
            return dst
        except OSError as e:
            if self.is_demoted(FilingArea.SHARE) or self.report_failure(FilingArea.SHARE, dst, e) is None:
                self.error(f"move: cannot create the destination of '{src}' ('{dst}'): {e}")
                return None
        dst = self._share_destination(Path(str(src)).as_posix())
        if dst is None:
            return None  # the share fallback refused: C: at its ceiling
        try:
            os.makedirs(os.path.dirname(dst.rstrip("/")), exist_ok=True)
        except OSError as e:
            self.error(f"move: cannot create the destination of '{src}' on the share fallback ('{dst}'): {e}")
            return None
        return dst

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
            # The queued destination is not used: it is decided again at the move (`_destination`).
            items = [src for src in Filer._pending if src not in Filer._in_flight]
        vanished = []
        for src in items:
            if not os.path.exists(src):  # already moved/cleaned elsewhere -- or lost with D:
                with Filer._pending_lock:
                    Filer._pending.pop(src, None)
                vanished.append(src)
                continue
            if MoveGuardian().is_protected(Path(src)):
                continue  # producer still writing; leave it for the next sweep
            destination = self._destination(src)
            if destination is not None and self.move(src, destination):
                with Filer._pending_lock:
                    Filer._pending.pop(src, None)
        if vanished:
            self.report_lost(vanished)

    def _sweep_loop(self):
        while True:
            # ensure_shared_root() (through _share_usable) rather than a bare probe: the
            # sweeper is what retries a backlog, and the backlog's usual cause is precisely
            # a missing per-machine root. With a share fallback it drains there instead.
            if self._share_usable():
                self._drain_pending()
                self.drain_fallbacks()
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


def _product_folders(root: str) -> list[str]:
    """Folders under ``root`` (not ``root`` itself) that directly hold a file that is not
    bookkeeping, deepest first -- spelled as ``root`` is spelled.

    Not realpaths: the callers re-root these with `Filer.area_of` / `to_top`, which compare
    against the roots' own spelling, and a realpath need not keep it -- macOS resolves
    /var to /private/var, and Windows resolves a mapped Z: to its UNC path (plans/
    filing-areas-design.md 2). Claims are checked by realpath where they are kept.

    A folder is a unit of relocation only if it directly holds files. Parents of such
    folders are left alone, so a claimed leaf cannot be carried off inside an unclaimed
    ancestor. Bookkeeping does not count: a folder holding nothing but its own sequence
    counter has no product to relocate, and treating it as a candidate is what made every
    sweep retry, and fail, forever.
    """
    top = os.path.normpath(root)
    found = []
    for dirpath, _dirnames, filenames in os.walk(top):
        if os.path.normpath(dirpath) != top and any(not is_bookkeeping(f) for f in filenames):
            found.append(dirpath)
    return sorted(found, key=len, reverse=True)


def _remove_empty_folders(root: str) -> None:
    """Remove every empty folder under ``root``, deepest first; never ``root`` itself."""
    real_root = os.path.realpath(root)
    for dirpath, _dirnames, _filenames in os.walk(real_root, topdown=False):
        if os.path.realpath(dirpath) != real_root:
            with contextlib.suppress(OSError):
                os.rmdir(dirpath)


_NIGHT_LABEL = re.compile(r"\d{4}-\d{2}-\d{2}")
#: A log file's name: mast-<role>-log.txt, or mast-<role>-<program>-log.txt.
_LOG_NAME = re.compile(r"mast-.+-log\.txt")


def _is_night_label(name: str) -> bool:
    """``yyyy-mm-dd`` and a real date -- what `observing_night` names a night's folder."""
    if not _NIGHT_LABEL.fullmatch(name):
        return False
    try:
        datetime.date.fromisoformat(name)
    except ValueError:
        return False
    return True


def _rename_logs_as_fallback(folder: str) -> None:
    """``mast-<role>-log.txt`` -> ``mast-<role>-log.fallback.txt`` directly in ``folder``."""
    for name in os.listdir(folder):
        if _LOG_NAME.fullmatch(name):
            target = os.path.join(folder, name[: -len("-log.txt")] + "-log.fallback.txt")
            if not os.path.exists(target):
                with contextlib.suppress(OSError):  # an open log stays; the next drain retries
                    os.rename(os.path.join(folder, name), target)


def _canonical(path) -> str:
    """``realpath``, case-folded where the file system ignores case. The path need not exist."""
    return os.path.normcase(os.path.realpath(path))


def _overlaps(a, b) -> bool:
    """True if ``a`` and ``b`` are the same path, or one lies inside the other."""
    ca, cb = _canonical(a), _canonical(b)
    return _is_under(ca, cb) or _is_under(cb, ca)


def _collision_key(entry: Path, target: Path) -> tuple:
    """Both sides' path, size and mtime: a pair that differed once differs until one of them changes.

    Only a *difference* is remembered under this key. A stale one keeps the source on the
    ram area, which loses nothing; a remembered "identical" could delete the only original.
    """
    entry_stat, target_stat = entry.stat(), target.stat()
    return (str(entry), entry_stat.st_size, entry_stat.st_mtime_ns, target_stat.st_size, target_stat.st_mtime_ns)


_COMPARE_CHUNK_BYTES = 1 << 20


def _same_bytes(a: Path, b: Path) -> bool:
    """Byte-for-byte equality, read fresh every time.

    Not ``filecmp.cmp``: it caches its verdict per process by path, size and mtime even
    with ``shallow=False``, and a share copy can change without a new mtime. A stale
    "equal" here deletes the only ram original.
    """
    if a.stat().st_size != b.stat().st_size:
        return False
    with a.open("rb") as fa, b.open("rb") as fb:
        while chunk := fa.read(_COMPARE_CHUNK_BYTES):
            if chunk != fb.read(_COMPARE_CHUNK_BYTES):
                return False
    return True


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


#: PathMaker's per-folder sequence counter. It belongs to the tree it is maintained in and
#: must not be relocated: the destination keeps its own, so moving it is a guaranteed
#: collision, and taking it off the ram side mid-night would reset the numbering there.
SEQUENCE_FILE_NAME = "seq.txt"


def is_bookkeeping(name: str) -> bool:
    """True for files that are the mechanism rather than the product.

    Three kinds, all of which must stay where they are:

    * `seq.txt` -- the sequence counter (see `SEQUENCE_FILE_NAME`).
    * `.<name>.lock` -- a MoveGuardian claim. Ram-local by design, and possibly still held.
    * `.<name>.partial` -- a copy in progress, or one a failed copy left (see `_move_file`).
      Never a product: the next attempt overwrites it.

    Without this the sweeper treated a folder containing only a counter as a relocation
    candidate, tried to merge it every pass, and reported two errors each time: the counter
    colliding with the destination's, then the source folder "not empty". Observed on mast00
    on 2026-08-17 flooding the log every 30 seconds, with 19 files stranded on the ram disk.

    The counter divergence that caused is the part that matters: the ram side had reached 3
    while the share still said 1. `D:` is a RAM disk, so on reboot its counter is gone and
    numbering restarts at 0 -- straight into the folders already on the share. The durable
    copy that exists to prevent exactly that was the stale one.
    """
    return name == SEQUENCE_FILE_NAME or (name.startswith(".") and name.endswith((".lock", PARTIAL_SUFFIX)))


PARTIAL_SUFFIX = ".partial"


def _partial_path(dst: Path) -> Path:
    """Where ``dst`` is copied before it is renamed into place: ``<dir>/.<name>.partial``."""
    return dst.with_name(f".{dst.name}{PARTIAL_SUFFIX}")


#: OSError values for a rename across volumes -- RAM disk to share, or to another drive.
_CROSS_DEVICE_ERRNOS = {errno.EXDEV}
_CROSS_DEVICE_WINERRORS = {17}  # ERROR_NOT_SAME_DEVICE
#: The rename `_move_file` uses; tests make it refuse, as a rename across volumes does.
_replace = os.replace
#: How the local disk's usage is read; tests replace it.
_disk_usage = shutil.disk_usage


def _move_file(src: Path, dst: Path) -> None:
    """Move one file so that ``dst`` appears whole or not at all.

    On one volume it is a rename, which is atomic. Across volumes it is copied to
    ``.<name>.partial`` beside ``dst``, renamed onto ``dst``, and only then removed from
    ``src``. A cross-volume `shutil.move` copied under the final name, so a share dropping
    mid-copy left a truncated file there -- which the next attempt read as a collision,
    reporting it and keeping the source, with the junk left behind
    (plans/filing-areas-design.md 6.2). A failed copy removes its partial and leaves the
    source untouched; raises what the copy raised.
    """
    try:
        _replace(src, dst)
        return
    except OSError as e:
        if e.errno not in _CROSS_DEVICE_ERRNOS and getattr(e, "winerror", None) not in _CROSS_DEVICE_WINERRORS:
            raise
    partial = _partial_path(dst)
    try:
        shutil.copy2(src, partial)
        _replace(partial, dst)
    except BaseException:
        with contextlib.suppress(OSError):
            partial.unlink()
        raise
    src.unlink()


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

    def drop_claims_under(self, root: str) -> int:
        """Forget every claim this process holds under ``root``, closing the handles and
        leaving the lock files alone -- for a root that is gone, with its lock files.
        Returns how many were dropped."""
        real_root = os.path.realpath(root.rstrip("/\\") or root)
        with MoveGuardian._claim_lock:
            dead = [real for real in MoveGuardian._folder_claims if _is_under(real, real_root)]
            handles = [MoveGuardian._folder_claims.pop(real) for real in dead]
        for handle in handles:
            with contextlib.suppress(Exception):
                _close_claim(handle)
        return len(dead)

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

            # A product recorded here and gone from here either reached the share or went
            # with the RAM disk; the reaper used to count both as moved. Ask, before the
            # records are forgotten.
            with self._condition:
                recorded = self._products_under(real_folder)
            Filer().report_lost(recorded)

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
