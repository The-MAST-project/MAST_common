# MAST Common -- Architecture Decisions

---

## [2026-07-02] PHD2 limit frame becomes persisted configuration (`phd2.limit_frame`)

**Why:** Whether PHD2 confines guide-star selection to a limit frame — and which
rectangle it uses — was controlled by code: an `ImagerSettings.use_set_limit_frame`
flag whose guiding-time value was effectively hand-edited on the production machine
(the `# oren` toggles in `MAST_unit`'s `phd2.py`), and a rectangle derived at runtime
from the fiber position and margins in `guiding.rois`. Operations needs to flip the
behavior and tune the rectangle without touching code.

**What:** Added `LimitFrameConfig` to `config/phd2.py` and a
`PHD2Config.limit_frame` field, persisted like every other unit setting in the
`units` collection ('common' doc + per-unit delta):

- `enabled` (default `True`) — whether to set a limit frame when guiding.
- `x`, `y`, `width`, `height` (defaults 0) — an explicit rectangle in unbinned
  camera pixels; `width`/`height` of 0 means "not configured"
  (`has_roi` is the accessor).

An explicit flat x/y/width/height shape was chosen (Oren offered either that or a
fiber+margins `SpecROI` shape) because it maps 1:1 onto both `ImagerRoi` and the
PHD2 `set_limit_frame` RPC, and 0-defaults represent "not configured" without
nullable nested models. Fields carry the `json_schema_extra` UI metadata
(per the `FocuserConfig` precedent) with `CAN_CHANGE_CONFIGURATION` capability, so
the GUI can expose them.

**Implications:** Existing DB documents parse unchanged: absent section ⇒
`enabled=True` with no rectangle, which consumers treat as "derive the frame from
`guiding.rois` as before". Consumers (currently `MAST_unit`'s
`PHD2Connector.start_guiding`) read the section via their `unit_conf` snapshot, so a
DB change takes effect on the next service restart.

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
