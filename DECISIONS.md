# MAST Common -- Architecture Decisions

---

## [2026-07-23] `ImagerRoi.verbatim()` — an unconditioned construction path

**Why:** `ImagerRoi.model_post_init` conditions every rectangle (center-preserving
shrink to camera alignment constraints, with the non-idempotence bug tracked in
#17). For PHD2's `set_limit_frame` that conditioning is both unnecessary — PHD2
applies the ZWO alignment constraints itself since upstream PRs #1374–#1376, and
the deployed MAST build (a master snapshot) includes them — and harmful: the limit
frame is a deliberately placed (possibly one-sided) band near the fiber, and any
shift or shrink defeats the placement. The interim mitigation (a WARNING naming
configured vs. applied values, 2026-07-22) made the mutation visible; this removes
it for the path that matters.

**What:** A `verbatim(x, y, width, height)` classmethod constructing through
`model_validate` with a validation-context key (`VERBATIM_ROI_CONTEXT_KEY`) that
`model_post_init` honors by returning before any conditioning. `_center` stays
unset (nothing reads it on this path). All existing constructors — direct,
`from_other`, deserialization — condition exactly as before.

**Implications:** MAST_unit's `mode: fixed` limit frame now reaches PHD2 exactly as
configured in the DB. #17 (conditioning non-idempotence, the −1 center bias)
remains real but its blast radius no longer includes the limit frame; its remaining
consumers are the derived/sky/spec ROI paths. New consumers needing an exact
rectangle should use `verbatim` rather than compensating for conditioning.

---

## [2026-07-23] `phd2.limit_frame` selects by `mode`, not an enabled-flag

**Why:** The initial shape (`enabled: bool` + zero-sentinel rectangle, entry below)
encoded three outcomes in two knobs, and read backwards at the operational moment:
`enabled: false` is the state the fold-mirror units actually want (full-frame star
selection — today's hand-patch), but it reads like "feature off", while
`enabled: true` without a rectangle silently *kept* the derived-ROI behavior the fix
exists to escape. An incomplete rectangle also degraded silently to the derived ROI.
Caught during deploy planning, before any merge/deploy — a rename, not a migration.

**What:** `LimitFrameMode` StrEnum discriminator replacing `enabled`:

- `mode: derived` (default) — limit frame from the fiber/margin-derived guiding ROI
  (deployed behavior; absent DB section still means this).
- `mode: full_frame` — no limit frame, full-sensor star selection.
- `mode: fixed` — the configured rectangle (unbinned camera pixels).

Validation now has teeth: `fixed` **requires** a complete rectangle (fail-fast at
parse), and a rectangle configured under any other mode is rejected as a
contradiction instead of being silently ignored. The `has_roi` sentinel accessor is
gone. The flat x/y/width/height shape, unbinned-pixel convention, and per-field GUI
capability metadata stay; `mode` carries a `select` widget with the three options.

**Implications:** Consumers dispatch on `mode` (MAST_unit's `start_guiding()` is a
three-arm `match`). Existing DB docs without the section parse unchanged. The Mongo
example in the 2026-07-02 entry below becomes
`{ "phd2.limit_frame": { mode: "full_frame" } }` (or `mode: "fixed"` + rectangle).

---

## [2026-07-22] Establish a pytest `tests/` harness; first suite guards `phd2.limit_frame`

**Why:** The PHD2 limit-frame work (#12) was validated by one-off bench scripts on
labcomp2 (2026-07-07); those runs proved the behavior but protect nothing against
future regressions. The repo had no test harness at all, so every model change was
re-verified by hand or not at all.

**What:** `tests/` with a pytest suite that drives the real code with **no Mongo
server and no hardware**, so it runs on any dev machine and in the unit venv:

- `tests/conftest.py` installs a module alias so the repo root imports as the
  `common` package from any clone (the directory is named `MAST_common` or
  `src/common`, never `common`). On **Darwin only**, it also shims
  `Filer.__init__` to a temp-dir layout — `Filer` supports Windows/Linux only and
  raises at import time on macOS (module-level `Filer()` in `common.utils`);
  the shim is a no-op on the deployed platforms and should be retired when real
  Darwin support lands.
- `tests/test_limit_frame_config.py` — the `LimitFrameConfig` contract: defaults
  equal today's deployed behavior, `has_roi` requires both dimensions, negative
  pixels rejected, GUI capability metadata present, and legacy `units` docs
  (shape taken from a real backup) parse unchanged without the section.
- `requirements-dev.txt` declares pytest (runtime deps stay with the consuming
  projects; this repo has no standalone installation).

**Implications:** New config-model work should add its cases here rather than as
bench one-offs; the labcomp2 bench remains for what genuinely needs a live PHD2 or
real camera. The suite is the durable home foreseen by the 2026-07-07 bench's
TEST-MIGRATION plan.

---

## [2026-07-09] Mongo URI composes the DNS domain (FQDN), not the bare controller_host

**Why:** `local.py`'s `mongo_uri` built `mongodb://{controller_host}:{port}` from the
bare hostname, while every other host in the stack is qualified by appending
`local.domain` (e.g. `api.py`'s
`controller_fqdn = f"{site.controller_host}.{load_local_config().domain}"`). The bare
form does not resolve off the controller's own subnet -- the exact failure a pre-config
interim fix had patched by hard-coding `mongodb://mast-ns-control.weizmann.ac.il:27017`.
When the TOML-config epic superseded that hard-coded line (`mongo_uri` now derives from
the file), the bare-host regression came back.

**What:** `mongo_uri` now returns `mongodb://{controller_host}.{domain}:{mongo_port}`,
matching the FQDN pattern used throughout the codebase. `controller_host` stays a bare
hostname in the site TOMLs and the DB `sites` docs; `domain` remains the single source
of truth for the DNS suffix.

**Implications:** The interim hard-coded-FQDN commit is fully reconciled and can be
retired. Any deployment must ensure `{controller_host}.{domain}` resolves (production
DNS already provides this). Verified end-to-end on the dev VM against a local Mongo:
`mongo_uri` composed to `mongodb://mast-ns-control.weizmann.ac.il:27017`, connected, and
cross-validated against the DB `sites` doc; the fail-fast paths (missing role/file,
malformed TOML, DB drift) each raised `ConfigError` and exited non-zero.

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

## [2026-06-21] Per-machine bootstrap config moves to a TOML file; site never derived from hostname

**Why:** `Config` hard-coded the MongoDB host (`mongodb://mast-wis-control:27017`),
the database name, the local-file path, and `NUMBER_OF_UNITS`, and it *deduced the
site by string-parsing the hostname* (`mastw` -> wis, `mast00`/`mast12` -> ns, etc.).
That made the site a brittle naming convention and scattered deployment facts across
constants and hostname heuristics. The DNS domain had three independent sources
(`Const.WEIZMANN_DOMAIN`, a `networking.WEIZMANN_DOMAIN` global, and `Site.domain`),
used inconsistently, which would silently break any non-`weizmann.ac.il` site.

**What:**

- New `config/local.py`: `LocalConfig` pydantic model (`site`, `project`,
  `controller_host`, `database`, `domain`, `location`, `mongo_port=27017`) plus
  `ConfigError` and a cached, MongoDB-free `load_local_config()`. The file is read
  from `C:\WIS\<role>.toml` (Windows) / `/etc/wis/<role>.toml` (*nix), where `<role>`
  is `MAST_PROJECT` (`unit`/`spec`/`control`); `MAST_CONFIG` overrides the path.
  See `config/local.toml.example`.
- **Site is never derived from the hostname.** The config file is the single source
  of truth; the hostname is used only for machine self-identity (which unit am I).
  Removed the hostname site-parsing block, `NUMBER_OF_UNITS`, and the hard-coded
  mongo/db/file values from `Config`. `local_site` now resolves by `local.site` name.
- **Config DB is MongoDB-only.** Dropped the local-JSON file backend entirely
  (the `mast-config-db.json` reader/writer, `load_from`/`DataSource`, file caches).
  Connection comes from `local.mongo_uri` / `local.database`; `DEFAULT_COLLECTIONS`
  is a module constant (DB schema, not a per-deployment setting).
- **Conscious duplication, validated.** `project`, `controller_host`, and `location`
  live in both the config file and the DB `sites` doc by design; `Config` cross-checks
  them at startup (`_validate_local_identity`) and raises `ConfigError` with the exact
  field diff if they disagree, so they can never drift silently.
- **Domain has one source.** Added `domain` to `LocalConfig`; deleted
  `Const.WEIZMANN_DOMAIN`, the `networking.WEIZMANN_DOMAIN` global, and the
  `Site.domain` field. All consumers use `load_local_config().domain` (or
  `self.local.domain`). Known consequence: FQDNs for *remote* sites
  (`api.py`, `assignments.py`) now use the local machine's domain — a single-domain
  assumption, true today (all `weizmann.ac.il`); a multi-domain deployment would need
  domain restored per-site.
- Removed `Site.local` (a redundant DB flag for "which site is us"); `local_site`
  answers that from the config file. Removed the dead parallel `config_toml.py` and
  the obsolete `mongo_seeds/` and `config/backup/` seeds.
- `notifications.py` `initiator` is now built lazily (PEP 562 `__getattr__` +
  `default_factory`) from `load_local_config()` + the `MAST_PROJECT` role, instead of
  parsing the hostname at import time — so config errors surface at the app's
  startup fail-fast point, not as an import error.

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
