# MAST Common -- Architecture Decisions

---

## [2026-08-31] A DB change takes effect within seconds, not at the next service restart

**Why:** `Config` read MongoDB once, at `__init__`, and never again. Everything downstream
read that frozen dict, so [2026-07-02] had to ratify the consequence -- "a DB change takes
effect on the next service restart" -- as though it were a design choice. It was a
side effect of the loading code.

The two TTL caches that looked like they refreshed something were **inert**. `mongo_cache`
(60 s) wrapped a loader reachable only from `__init__`, so its TTL never caused a re-read.
`config_db_cache` (30 s) wrapped a method whose body was `return self.db`. That is why
`set_unit` calling `clear_mongo_ttl_cache()` did not let a process see **its own write**,
and why `MAST_control`'s 30 s "config refresh" timer (`controller.py:526`) rebuilt `Site`
objects from an unchanging dict every 30 s for as long as the controller ran.

**What:** a `ConfigSnapshot` published by whole-reference assignment, refreshed by a
`config-watcher` daemon thread that follows a MongoDB change stream and falls back to
polling. Five decisions inside it are load-bearing:

- **An event is a trigger, never data.** A delete carries only `documentKey._id`, and
  `_id` is projected out of every stored document, so an event cannot be applied
  incrementally -- only used to decide which collection to re-read. Everything else
  follows from this.
- **No resume tokens, ever.** pymongo already resumes across ordinary blips. What it
  cannot resume -- `invalidate`, `ChangeStreamHistoryLost`, a failover -- all want the
  response the outer loop already makes: drop, re-read everything, reopen from now. Since
  events are triggers, a gap costs latency, not correctness. A persisted token would buy
  nothing (startup re-reads regardless) and would add a stale-token recovery path.
- **`directConnection=True`.** `rs0`'s sole member advertises itself as the bare
  `mast-ns-control:27017`, which per [2026-07-09] does not resolve off the controller's
  subnet. Replica-set discovery would replace our FQDN seed with that name and every unit
  would lose the database. Verified that change streams work over a direct connection.
- **Change detection compares documents, not a fingerprint.** The obvious fingerprint,
  `(count, max _id)` -- which the fleet's own `MAST_config_db` monitor uses -- cannot see
  an in-place edit to an existing document, which is exactly what an operator changing a
  value produces. The whole database is 18 KB; `!=` is exact and affordable.
- **One event, one reload; coalescing lives on the notification side.** The first version
  collected bursts by calling `try_next()` again, which blocks for the full idle timeout
  when nothing further is waiting -- so it delayed *every* change by that timeout
  (measured: 10.03 s against `rs0` for a change the server delivered in 0.02 s).

Caching is now keyed on the **generation of the collections an accessor reads**, not on
time. A time-keyed cache is wrong in both directions at once: too eager (rebuilds an
unchanged model when the clock runs out) and too lazy (serves a stale one for the rest of
the window). The generation key also gives accessors an identity property the design
relies on -- within one generation an accessor returns *the same object* -- so **an
operation uses the configuration it started with** simply by binding `conf = ...` once at
entry. No copying, no locking, no "which view am I on" bookkeeping.

Two behaviour changes ride along, both in `set_unit`: it now **reloads `units`
synchronously** after a successful write, so a process finally sees its own write even
with the watcher off; and it **raises `ConfigError` on write failure** instead of logging,
because all three callers went on to log "saved ..." after a lost write.

**Implications:** [2026-07-02]'s closing note is superseded. `start_watching()` is
**opt-in** -- a thread holding a change-stream cursor needs an owner with a lifetime, and
`Config()` is constructed by things that have none (a `manage.py` one-shot, a `--help`, a
test), so each long-running service calls it once from its startup path. Consumers that
snapshot config into `self.conf` at construction stay stale until converted; that is what
makes the migration safe to do per repo, given nothing pins `common` to its consumers
(#34). Anything mutating a value returned by an accessor is now a defect rather than a
wart -- it would change the model for every reader and then be silently reverted at the
next generation -- so read-modify-write goes through `update_unit()`, which hands the
mutator a private copy. MAST_unit#195 tracks the one site that cannot simply be converted.

---

## [2026-08-31] The local config cache is a boot crutch, not a backend

**Why:** [2026-06-21] deleted the local-JSON config backend and made MongoDB the only
source, which was right: a file you could edit was a second authority that could disagree
with the database. But it left an unreachable controller as a fatal startup error, and the
failure was not even a clean refusal -- `MAST_unit`'s `app.py` takes its own listen
address from the DB, so nssm restarted a process that died before it could listen. The
operator saw a running service, an unanswered port, and a traceback they had to go find
(#82).

**What:** a directory of timestamped copies under `~mast/MAST/config-db-cache/`
(`C:\MAST\config-db-cache\` on Windows), newest 10 kept, with a `latest` pointer. Written
only by the watcher and only from a successful MongoDB read; read only at startup and only
after MongoDB has already failed. Both sources failing is still fatal.

It is not the backend [2026-06-21] deleted, and three properties keep that true: a
hand-edit cannot reach any system that can reach MongoDB, because the first successful read
overwrites it; it is never a source anything prefers; and a copy written against a
different `mongo_uri` or database is refused, so a cache carried between machines cannot
boot one on another site's configuration.

Details that are decisions rather than mechanics:

- **The filename carries a UTC timestamp, not the generation.** The generation is a
  per-process counter that restarts at 0, so two machines would write different content
  under one name and a restart would overwrite its own history. `time_stamp()` cannot be
  reused for this: its ISO colons are illegal in Windows filenames, and the units are the
  Windows machines.
- **Startup picks the newest by filename sort, not by following `latest`.** On Windows
  `latest` is a *copy* -- creating a symlink needs a privilege the service account does
  not have -- so it cannot say which copy it is. `latest` is for a person reading the
  directory.
- **No maximum age.** A three-week-old cache that lets a unit close its covers beats a
  fatal startup. Staleness is reported through `ConfigHealth`, not punished.
- **`set_unit` refuses while degraded**, or a saved autofocus position would be diffed
  against a cache with nothing behind it and silently lost.
- **Not `LocalConfig.data_root`**, whose Linux branch is `/var/mast`: that directory does
  not exist on the control host and `/var` is not writable by the service account, so the
  cache would silently never be written on the one platform where nothing else would
  notice. Not `Filer().local` either -- on Linux that is the *share*, one directory common
  to every host, which is the opposite of what a per-machine boot cache needs.

**Implications:** startup is now fatal only when both MongoDB and the cache fail, which is
a deliberate narrowing of "a configuration failure at startup is fatal", not an abandonment
of it. Degraded is loud: one ERROR naming the MongoDB failure and the cache's age, and
`ConfigHealth` reports it for as long as it lasts. Services should surface `ConfigHealth`
on their status endpoint -- a unit running on last week's configuration looks entirely
normal otherwise.

---

## [2026-08-16] A handler built at registration declares itself with the same token

**Why:** MAST_unit#117 registers a route on a closure built after construction --
`endpoint=self._spiral_new_path_endpoint()` -- rather than on a method. The reason is sound
and cannot be worked around: the operator-facing defaults are that unit's own configured
fibre position, and a signature default is evaluated at import, long before `Config()` has
loaded. Building the handler in a closure is what puts real numbers in Swagger instead of a
placeholder. But `add_api_route` refuses any callable carrying no declaration, so the two
collide the moment that work and MAST_unit's `eli/endpoint-contract` meet: whichever merges
second stops the process at import.

Refusing the pattern outright was the alternative, and it was rejected -- it would trade a
real operator affordance for a mechanism's convenience. A nested function is also exactly the
shape the `endpoint_` prefix used to hide, having no method name to scan, so the mechanism
owes it an answer rather than a prohibition.

**What:** `@endpoint(..., factory=True)` declares a method that *builds and returns* the
handler. The declaration then rides on both halves: the factory keeps it, so
`declared_endpoints`' class-attribute scan still enumerates the surface; and each handler the
factory produces is stamped as it is built, so `add_api_route` accepts what it is handed. A
factory returning something that is not callable raises `UndeclaredEndpointError` naming the
factory, rather than failing later inside FastAPI where neither the cause nor the site is
visible.

It is a **flag on the existing decorator, not a second decorator**. The prefix's retirement
was ratified on the condition that one literal `@endpoint(` grep returns the surface exactly;
an `@endpoint_factory(` would not match that grep, and would put a hole in the property on
the day it was introduced.

**Implications:** invariant 10 now covers the whole surface rather than the part that happens
to be defined at import, and the enumeration MAST_unit#39, #40 and #52 consume stays
complete. The unit's static routes-to-declarations check learns the call form
(`endpoint=self.<factory>()`) alongside the attribute form. Config-derived OpenAPI defaults
become a supported pattern rather than an accident, which is likely to recur: every per-unit
value an operator should see pre-filled in Swagger has the same import-order problem.

---

## [2026-08-11] The HTTP surface is declared at the definition site, not by a name

**Why:** MAST_unit's `endpoint_` prefix was meant to make a component's HTTP surface visible
by scanning its method names. Measured on `65a1b96` it did not: of 73 routed operations, **26
were registered on bare, unprefixed methods**, and ten `endpoint_`-named methods were routed
by nothing at all. A convention that looks authoritative and is wrong in both directions is
worse than none, because it is trusted. The retirement was ratified 2026-08-10 (Eli, Arie
aware) on the condition that whatever replaced it actually delivered the quick-find property
the prefix was chosen for.

**What:** `common/endpoints.py` -- a `Tier` enum, an `@endpoint(tier=...)` decorator that
marks a method at its definition, `declared_endpoints()` to enumerate the surface at runtime,
and an `add_api_route()` helper that **raises `UndeclaredEndpointError` at import** when a
route is registered on an undeclared method.

Four choices inside it are load-bearing:

- **Keyword-only decorator arguments.** `@endpoint(Tier.INTERFACE)` would read naturally as
  positional and invite a bare `@endpoint`. One literal `@endpoint(` must find the whole
  surface exactly, so there is deliberately no form that omits the parenthesis.
- **`declared_endpoints()` walks `vars()` across the MRO, never `getattr` on the instance.**
  A component is full of properties that touch hardware -- `connected` talks to an ASCOM
  driver -- so plain attribute access during a scan would connect a telescope as a side
  effect of asking a question about the class.
- **`tags` passes through untouched.** Replacing subsystem tags with the tier is
  MAST_unit#39; doing it here would make MAST_unit#34 stage 2's OpenAPI snapshot diff
  unreadable, and that diff is the only mechanical guard over a 62-registration rewrite.
- **`Stability.DEPRECATED` drives FastAPI's native `deprecated=True`**, which renders struck
  through in Swagger. That is the retirement notice for the eleven routes MAST_unit#124
  removes, and it reaches operators a code search cannot.

**Implications:** the marker is the single source MAST_unit#39, #40 and #52 read, replacing
three independent re-derivations of the route set from `api_router` bodies. Registration
becomes the one seam where the response envelope can later be applied once instead of
per-handler (MAST_unit#34 stage 3), which is also the seam the parked HTTP-status-code
decision needs.

One measured consequence worth recording, because it corrects a claim made when stage 2 was
planned: **`deprecated=True` is not schema-neutral.** It adds a `deprecated` key to the
operation. So stage 2's guarantee is not "the OpenAPI schema is byte-identical" but "identical
except an additive `deprecated: true` on exactly the eleven known routes", and its snapshot
check asserts that narrower thing.


---

## [2026-08-09] An imager backend's `status()` answers for itself, not for the imager

**Why:** `ImagerInterface` never declared `status` at all, so each of the three
backends invented a meaning for it. PHD2 returned a narrow `PHD2ImagerStatus` and
took a `capacity: Literal["imager", "guider"]` argument selecting between two
different return models; ASCOM and ZWO returned a whole `ImagerStatus` and took no
argument. The `Imager` wrapper called `self._backend.status(capacity="imager")`
against all three, so `/imager/status` raised `TypeError` on ASCOM and ZWO alike
(MAST_unit#100) — a 500 on two of the three backends, silenced at the call site by
a `# type: ignore`. Where it did not raise, it nested a full `ImagerStatus` inside
the wrapper's own under `backend`, answering temperature, cooler, set point and
camera size twice with only the outer copy authoritative. Nothing caught that,
because the field was typed `object | None`.

**What:** Two declarations, and the meaning follows from them.

- `ImagerBackendStatus` is what a backend reports about *itself*. It derives
  `ComponentStatus` — a backend **is** a `Component`, since `ImagerInterface`
  derives from it — and declares only the two fields genuinely its own,
  `identifier` and `name`. `connected`, `operational`, `why_not_operational`,
  `activities` and `activities_verbal` all come from the base.
  `PHD2ImagerStatus` becomes a subclass that only pins `name = "phd2"`.
- `ImagerInterface.status() -> ImagerBackendStatus` is declared abstract, and
  `ImagerStatus.backend` is typed `ImagerBackendStatus | None` rather than
  `object | None`.

The composite stays the `Imager` wrapper's job: it already computes the general
fields itself and reaches into the backend separately for `set_point`. This is not
a new pattern — the guider side has had a typed `GuiderStatus.backend:
PHD2GuiderStatus` all along. The imager side is the one that drifted.

`capacity` disappears rather than moving: PHD2's two roles become two methods
(`status()` for the imager role, a separate accessor for guider status), which
removes the by-kind dispatch, the union return type, both `# type: ignore`s, and a
latent `UnboundLocalError` on an unmatched capacity. The two branches shared only
`identifier`, so the split duplicates nothing.

**Implications:** ASCOM and ZWO must stop returning `ImagerStatus` from
`status()` — a behavioral change to what `/imager/status` reports under `backend`,
made deliberately. Checked before making it: nothing consumes the field.
`MAST_control` has no reference to it and every `backend` hit in `MAST_gui` is
Django's own vocabulary. Eli confirmed the ASCOM and ZWO paths are not working
today regardless.

Deriving `ComponentStatus` also changes the backend payload, in the same
no-readers way: it gains `type`, `detected` and `was_shut_down`, and the base's
optionality replaces the narrower defaults — `operational` and
`why_not_operational` default to `None` rather than `False` and `[]`, while
`activities` defaults to `0` rather than `None`. Accepted rather than papered
over by re-declaring the fields, since re-declaring them is exactly the
duplication being removed.

The root cause is one level up and is **not** fixed here: `Component.status()` is
declared with no return annotation at all, which is why *every* component's status
is free to drift. Annotating it reaches `MAST_control` and `MAST_spec`, which also
implement `Component`, so it is deliberately sequenced behind the unit contract
rather than done in passing — #45, enforced by MAST_unit#52. Re-parenting
`ImagerBackendStatus` here is that issue's step 1, and the reason the base
annotation will hold when it lands: every other component's status model already
derives `ComponentStatus`, and the imager backends were the one exception.

The former body of the abstract `start_exposure` — the "must call
`start_exposure_series()` first" guard — moves to `require_open_exposure_series()`.
It has never executed: no backend calls `super().start_exposure()`. It is extracted
rather than deleted so the abstract method can declare its return type while the
guard stays callable, but it remains **unenforced**.

Related: MAST_unit#42 invariant 4 (uniform response envelope), MAST_unit#74,
MAST_unit#100.

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

## [2026-07-20] Machine role moves from the MAST_PROJECT env var into a `machine_role` field; fixed-path config.toml

**Why:** the machine role was carried by the `MAST_PROJECT` environment variable,
which every launcher (NSSM service, `.bat`, `docker-compose`, manual shell) and
every host had to set/persist — the friction that made spec/control/gui hard to
stand up, and the reason two apps on one box (gui + control on the controller)
could disagree about identity. The name also collided with the *project* concept
(`MAST_PROJECT=mast` raised `ConfigError`). A separate proposal to rename it to
`MAST_ROLE` (MAST_common#10) only moved the collision; the topology epic proposed
yet another `MAST_ROLE`. Epic #15 resolves all of it.

**What:** `local.py` no longer reads any env var for the role. `_config_file_path()`
returns the **fixed** path `C:\WIS\config.toml` / `/etc/wis/config.toml`
(`MAST_CONFIG` still overrides for dev/VM/tests). `LocalConfig` gains a **required**
`machine_role` field validated against `VALID_MACHINE_ROLES = (unit, spec, control)`
(renamed from `VALID_ROLES`; named `machine_role` to stay distinct from the *user*
role in `UserConfig`/`GroupConfig`). `notifications._build_initiator` and
`mast_logging.init_log` now read `local.machine_role` instead of `os.getenv`.
`init_log` runs at import, so it loads the role through a guarded lazy
`load_local_config()` and falls back to `mast-STARTUP-log.txt` when the config is
not yet readable. `machine_role` is deliberately **not** added to the DB `sites`
cross-check (a site hosts several roles). Supersedes MAST_common#10 (closed).

**Implications:** breaking bootstrap change — a machine on new code with an old
`<role>.toml` (wrong filename, no `machine_role`) fails fast at startup, by design.
Provisioning must write `config.toml` with an injected `machine_role` and stop
setting the env var (epic #15 Stage 2); consumers bump the `common` submodule and
drop their env-setters (Stage 3); the two on-site Linux hosts get an
`/etc/wis/config.toml` placed by hand (Stage 4). The env var is fully inert
afterward. Full plan: `docs/config-toml-role-plan.md`.

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
