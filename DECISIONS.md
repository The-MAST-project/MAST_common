# MAST Common -- Architecture Decisions

---

## [2026-06-25] Write-safe, audited RAM-disk -> shared transfer (`Filer`)

**Why:** `Filer.move_ram_to_shared` (the `ram-to-shared-mover`) persists acquisition
products to the shared store and, because it *moves*, also empties the RAM disk. The
2026-06-24 unit session exposed three faults (The-MAST-project/MAST_unit.2024-12-12#18):
fire-and-forget per-path threads raced the producers and each other (`move: path does not
exist` on every sequence -- a frame missed this way is lost once the volatile RAM disk is
wiped); the success log was commented out, so there was no positive proof a frame reached
the share; and `solve-field` scratch dirs were never swept. An interim fix inferred "the
writer is done" from file-size stability, but operator (Arie) feedback was that a
size-stability heuristic is not robust enough (a writer that stalls longer than the poll
interval looks finished).

**What:** Completion is now an **explicit contract**, not a guess.
- `atomic_path(final)` (context manager) -- producers write to `<final>.part` and it is
  `os.replace`d to `<final>` only after the writer closes. A file under its final name is
  therefore complete by construction; the temp is removed on error, so a partial never
  appears under the final name. `PART_SUFFIX = ".part"`.
- `move()` -- skips `*.part` sources; waits on **existence** (a sound signal now that
  publishes are atomic) rather than size; publishes the destination atomically too (stage
  to `<dst>.part`, then `os.replace`) so a reader on the share never sees a partial during
  the cross-volume copy; folder moves publish each finished file and skip in-flight
  `*.part`. Logs every successful move (the audit trail) and returns a bool.
- `move_ram_to_shared()` -- one serialized background worker per call (class-level
  `_move_lock`), so movers never race each other; logs a `moved X/N` reconciliation for
  multi-file calls.
- `clean_ram_tmp()` -- sweeps `<ram>/tmp/tmp_*` solver scratch.
- Integration tests in `tests/test_frame_transfer.py` drive the real `Filer` against temp
  dirs + threads (no hardware) and assert the contract (a reader never sees a partial
  final, temp cleaned on error, `*.part` skipped, atomic destination, reconciliation).

**Implications:** Robustness now depends on producers writing through `atomic_path` -- a
raw direct write is no longer size-guarded, so new product writers MUST use it (see the
File storage section in `CLAUDE.md`). Both atomic renames are intra-volume (the only place
`os.replace` is atomic); the cross-volume hop is the staged copy, never visible under a
final name. `solve-field`'s outputs are the one deliberate exception (written by the
external process, complete once it exits). Backward compatible: a file written the old way
still moves. Paired consumer-side adoption lives in the MAST_unit PR. Still deferred (not
addressed here): a reconciler/retry backstop for moves that fail at the destination, and
replacing the persistent `Z:` mapping with a per-operation UNC connect.

---

## [2026-05-16] DliPowerSwitch tolerates unresolvable hostname at construction and probe

**Why:** `DliPowerSwitch.__init__` was calling `socket.gethostbyname()` and re-raising
`socket.gaierror` when the hostname could not be resolved. This caused the caller
(`PowerSwitchFactory.get_instance()`) to raise, which propagated through `Unit.__init__`
and could crash the unit service at startup when the power switch was unreachable (e.g.
during provisioning tests or when the switch is offline).

The power switch is a best-effort component: the unit should start and operate (with
degraded capability) whether or not the switch is reachable. Failing to resolve the
hostname is not a reason to abort startup.

**What:**

`dlipowerswitch.py`
- `__init__`: `socket.gaierror` is now caught and logged as a warning instead of
  re-raising. `self.ipaddr` remains `None` when resolution fails. `self.base_url` is
  set to `http://None/` in that case (never used because `probe()` early-returns).
- `probe()`: added an early return at the top (`if not self.ipaddr: return`) so that
  a switch with an unresolved hostname is silently skipped during the periodic probe
  loop rather than generating HTTP errors or exceptions.

**Implications:** A `DliPowerSwitch` instance is always constructible regardless of
network state. Callers must check `detected` / `connected` / `operational` to know
whether the switch is actually available; they must not assume that successful
construction implies reachability. The warning logged at construction is the only
signal that resolution failed -- no exception is raised.

---

## [2026-05-16] Hostname casing and `mast-<site>-NN` canonical form

**Why:** Windows `socket.gethostname()` returns the hostname in uppercase
(e.g. `MAST-WIS-01`), but `unit_ids` in MongoDB and all our internal config keys are
stored in lowercase. Without normalisation, every Windows unit failed `Config.get_unit()`
and site-membership checks. Separately, the canonical unit hostname format was being
formalised as `mast-<site>-NN` (numbered units 1-20) alongside the existing
control/spec roles, but `canonic_unit_name()` only handled the role-based form.

**What:**

`config/__init__.py`
- `Config.__init__`: lowercase `socket.gethostname()` before site detection; extended the
  site-detection regex to match `mast-<site>-NN` in addition to control/spec roles.
- `get_unit()`: lowercase `unit_name` on entry, so callers passing the OS-supplied
  hostname (any casing) resolve against lowercase `unit_ids`.
- `site_name_from_unit_name()` and `_verify_unit_site_membership()`: lowercase
  `unit_name` before comparing against stored ids.

`utils.py`
- `canonic_unit_name()`: added a branch matching `mast-<site>-NN` via regex on the
  suffix, accepting unit numbers 1-20.

**Implications:** Treat hostnames as case-insensitive throughout the codebase -- always
`.lower()` before comparing against config keys or `unit_ids`. New helpers that take a
hostname argument should normalise at the entry point, not push the burden onto callers.
`canonic_unit_name()` now recognises both legacy role-based and numbered forms; do not
add new hostname schemes without updating it.

---

## [2026-05-14] `ensure_process_is_running` hardened for Windows VM provisioning

**Why:** The helper was the single startup path used by every MAST service to spawn
sidecar processes (PWI4, ps3cli, etc.) on the Windows VMs. Three failure modes were
biting provisioning tests: (1) paths with spaces were getting split by `cmd.exe` because
the `.exe` portion was unquoted, so processes silently failed to launch; (2) the helper
swallowed all `subprocess.Popen` exceptions with a bare `except: pass`, so launch
failures left callers spin-waiting forever in `find_process` with zero diagnostic output;
(3) every spawned process flashed a `cmd.exe` console window on the Windows desktop,
which was both ugly and a real problem when the unit was running as a Windows service.

**What:**

`process.py` (`ensure_process_is_running`)
- Path quoting for `shell=True`: split the command at `.exe` and quote the executable
  portion if it contains spaces, before handing to `subprocess.Popen(shell=True)`. The
  original `cmd` string is preserved for matching and logging; `cmd_for_shell` is the
  quoted form actually executed.
- Suppress the console window: pass `creationflags=subprocess.CREATE_NO_WINDOW` on
  Windows (sentinel-guarded so it's a no-op on Linux/macOS) for both `shell=True` and
  `shell=False` branches.
- Replaced `except Exception: pass` with logging the exception (to the supplied logger
  if present, else the root logger) and returning `None` so callers can detect failure.
- Added a `startup_wait_s` parameter (default 30) and a deadline-based wait loop:
  - polls `process.poll()` each iteration; if the child exited immediately, logs the
    return code and returns `None` instead of spinning;
  - returns `None` when `time.monotonic()` passes the deadline, with a timeout log line
    that names the pattern being waited on.
- Returns the discovered `psutil.Process` on success (previously fell off the end with
  no return value).

**Implications:** Callers must now handle `None` from `ensure_process_is_running` --
either by propagating the failure to their startup-error pipeline or by deciding the
sidecar is non-critical and continuing. Do not reintroduce a bare `except` here: any
new failure path must log and return `None`. When adding new sidecars, pick a
`startup_wait_s` matched to the process's real startup time -- the default 30s is tuned
for ps3cli/PWI4-class binaries.
