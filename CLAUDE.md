# MAST Common — Shared Claude Guidance

This file is part of `MAST_common`, which each MAST project consumes. It is imported by each project's own `CLAUDE.md` via `@../common/CLAUDE.md`.

## What is MAST?

**MAsters of Spectra** — a distributed telescope control system for the Multiple Aperture Spectroscopic Telescope. Several Python services communicate over HTTP (FastAPI), coordinated by a central controller.

## Project Structure

| Project | Role | Runs on |
|---|---|---|
| `MAST_common` | Shared library (sibling clone in every project) | — |
| `MAST_control` | Central backend orchestrator | `mast-ns-control` |
| `MAST_spec` | Spectrograph control backend | `mast-ns-spec` |
| `MAST_unit.*` | Per-unit backend (telescope hardware) | Each unit machine (`mast01`…`mast20`) |
| `MAST_gui` | Django web frontend | `mast-ns-control` |

The active site is **`ns`** (Neot Smadar), which is where those names come from. They are
recorded here for orientation only — **never read a hostname out of this table in code.**
The config DB is the source of truth: `Config().get_sites()` gives `controller_host` and
`spec_host` per site, and `Filer` builds the shared root from `socket.gethostname()`. This
table previously said `mast-wis-control` / `mast-wis-spec`, and that error was copied into
three other repos' `CLAUDE.md` files and into live code (MAST_control#21, MAST_gui#20).

### How each project gets `MAST_common`

**Every project consumes it as a sibling clone, in a flat layout. There is no submodule
anywhere any more.**

| Project | Path |
|---|---|
| `MAST_unit.*` | `<top>/common/`, beside `<top>/unit/` |
| `MAST_control` | `<top>/common/`, beside `<top>/control/` |
| `MAST_spec` | `<top>/common/`, beside `<top>/spec/` |
| `MAST_gui` | `<top>/common/`, beside `<top>/gui/` |

`import common` is satisfied by the `mast.pth` that MAST_provisioning writes into the venv,
which puts `<top>` on `sys.path`. **A clone of any single repo is therefore not runnable on
its own** — it needs the flat layout and that `.pth`.

The submodule was retired in MAST_unit#94 (2026-08-06), then MAST_spec#33, MAST_control#19
and MAST_gui#18. In every case the gitlink was **resolving nothing**: the `.pth` already
provided `common`, so the submodule was a second, stale mechanism shadowing the one actually
in use — `MAST_control` and `MAST_spec` were both pinned at the same commit, several merges
behind. An *uninitialised* submodule directory is also a live hazard: it is an empty
directory that Python can treat as a namespace-package portion named `common`, and it makes
ruff's first-party classification machine-dependent.

Keep `known-first-party = ["common"]` in each `ruff.toml`. It matters **more** now, not
less: the package lives outside every repo, where ruff's path-based resolver cannot classify
it at all. It is not cosmetic — adding it cleared 9 pre-existing `I001` findings in
`MAST_spec`, 3 in `MAST_gui` and 2 in `MAST_control`.

**Nothing now records which `MAST_common` commit a consumer was built against.** The gitlink
was that record, for all its faults. See MAST_common#34 for the gap and the options; #11
(publish as a versioned package) is the eventual answer.

## Configuration System (`common/config/`)

Two layers:

1. **Bootstrap — `common/config/local.py`.** A per-machine TOML file is the single
   source of truth for the machine's identity and how to reach the database. It is
   read from the fixed path `C:\WIS\config.toml` (Windows) / `/etc/wis/config.toml`
   (*nix); set `MAST_CONFIG` to override the path (dev/VM/tests). There is **no**
   `MAST_PROJECT` / `MAST_ROLE` env var — the machine's role is the required
   `machine_role` field (`unit`, `spec`, or `control`; distinct from the *user* role
   in `UserConfig`/`GroupConfig`), validated against `VALID_MACHINE_ROLES`.
   `load_local_config()` parses it into a `LocalConfig` (`site`, `project`,
   `machine_role`, `controller_host`, `database`, `domain`, `location`, `mongo_port`)
   — cached and MongoDB-free. On any problem it raises `ConfigError` with a detailed
   reason; apps should fail startup on that.

2. **Config DB — `Config` (`common/config/__init__.py`), a singleton.** Loads the
   configuration collections from **MongoDB only** (no local-file fallback), at
   `local.mongo_uri` / `local.database`. At startup `Config` cross-checks the local
   config against the DB `sites` document (`project`, `controller_host`, `location`
   are intentionally duplicated) and raises `ConfigError` on any mismatch, so the two
   sources cannot drift.

The site is **never** derived from the hostname — it comes from the config file. The
DNS `domain` likewise has a single source (`local.domain`).

Key `Config` methods: `get_unit()`, `get_sites()`, `get_service()`, `get_specs()`,
`get_users()`, `local_site`.

## API Conventions

### URL paths (defined in `common/const.py`)
- Units: `/mast/api/v1/unit/...`
- Control: `/mast/api/v1/control/...`
- Spec: `/mast/api/v1/spec/...`

### `CanonicalResponse` (`common/canonical.py`)
All API endpoints return a `CanonicalResponse`:
```python
class CanonicalResponse(BaseModel):
    api_version: str = "1.0"
    value: Any | None = None  # present on success
    errors: list[str] | None = None  # present on failure
```
Use `response.succeeded` / `response.failed` / `response.is_error`. `CanonicalResponse_Ok` is a convenience constant for `value="ok"`.

### `ApiClient` (`common/api.py`)
Wraps `httpx` for inter-service HTTP calls. `UnitApi`, `SpecApi`, `ControllerApi` are typed wrappers around `ApiClient`. `ApiResponse` converts JSON dicts to attribute-access objects.

## Component Architecture (`common/interfaces/components.py`)

All hardware components (Mount, Focuser, Camera, Covers, Stage, Spectrographs) implement the `Component` ABC which combines:
- `ABC` — requires `startup()`, `shutdown()`, `is_shutting_down`, `status`, `is_operational`
- `Activities` — bitflag-based activity tracking (`IntFlag`) with timing

`ComponentStatus` is the Pydantic status model: `detected`, `connected`, `operational`, `activities`, `why_not_operational`.

Each component exposes a `FastAPI` `APIRouter` (`api_router`) that is included in the main app.

## Logging (`common/mast_logging.py`)

Use `init_log(logger)` after getting a logger. Logs rotate daily under:
- Linux: `/var/log/mast/<date>/`
- Windows: `%LOCALAPPDATA%/mast/<date>/`

Rich console output is enabled by default.

## Notifications (`common/notifications.py`)

`Notifier` / `UiUpdateNotifications` push WebSocket events to the Django GUI. The `NotificationInitiator` is built lazily from the config file (`local.site`, `local.project`, `local.machine_role` for the machine type) — not from the hostname. The hostname is used only as the initiator's own machine name.

## Plans (`common/models/plans.py`)

Plans are observation jobs stored as TOML files named `PLAN_<ULID>.toml`. State is represented by which **subfolder** the file lives in under the plans directory — transitions physically move the file.

### States and allowed transitions
```
pending → in-progress → completed
                      → failed
        → postponed
        → deleted
expired / failed / completed / canceled / postponed / deleted → pending  (revive)
in-progress → canceled
```

`Planner` (singleton) owns one `PlansFolder` per state. File-system watching is **not yet implemented** — folders are only refreshed explicitly after each transition.

### `Plan` model fields
- `ulid` — auto-generated ULID, also encoded in the filename; enforced on load
- `target` — celestial target
- `spec_assignment` — `SpectrographModel` describing the spectrograph configuration
- `requested_units` / `allocated_units` — unit names involved
- `quorum` — minimum operational units required (default: 1)
- `timeout_to_guiding` — seconds to wait for all units to reach guiding (default: 600)
- `autofocus`, `too` (Target of Opportunity), `approved`, `production`
- `constraints` — scheduling constraints
- `events` — audit log appended back into the TOML file as `[[events]]`

## Conventions

### `json_schema_extra` formatting
In `json_schema_extra` dicts on Pydantic model fields, put one key-value entry per line, and never wrap a `"tooltip": "..."` value across lines — keep the whole tooltip pair on a single line regardless of length (prevents auto-formatter line-wrapping of tooltip content).

### Syncing `common/` across checkouts

Each machine has **one** `common/` clone, shared by every project beside it in the flat
layout — there is no longer a separate copy per superproject. If you do have more than one
checkout (say a second workspace), they are independent clones of the same repository, so
after changing any file under a `common/`, apply the same change to (or re-sync) the others
so they don't diverge.

### Updating `common/`

```bash
git -C <top>/common pull
```

That is the whole procedure. There is no gitlink to bump, no `git submodule update --remote`,
and no detached-HEAD trap — the clone sits on `master` like any ordinary repository.

Two consequences worth remembering:

- **A pull updates every consumer on that machine at once**, since they all share the clone.
  There is no per-project pin.
- **A consumer can reference an API the local clone does not have yet**, and nothing reports
  it — the import simply fails at runtime. This has already happened once on a dev box: a
  `common/` 24 commits behind, against a `MAST_control/app.py` already using
  `configure_logging` / `get_logger`. If a consumer raises `ImportError` or `NameError` on a
  `common` symbol, check the clone is current before anything else. See MAST_common#34.

## Project-wide LLM guidance

Cross-repo LLM guidance for MAST lives in the **`mast-claude-config`** repo (`github.com/The-MAST-project/mast-claude-config`) — the overarching home for project-wide instructions (shared coding standards, team working-style, global environment facts), deployed into `~/.claude/` by its `setup.sh`. Keep repo-specific guidance in the per-repo `CLAUDE.md`; put genuinely cross-repo guidance there. See `mast-claude-config/CLAUDE.md` for what belongs where.
