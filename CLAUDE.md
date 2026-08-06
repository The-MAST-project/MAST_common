# MAST Common — Shared Claude Guidance

This file is part of `MAST_common`, which each MAST project consumes. It is imported by each project's own `CLAUDE.md` via `@common/CLAUDE.md` (or `@../common/CLAUDE.md` in MAST_unit, which no longer submodules it — see below).

## What is MAST?

**MAsters of Spectra** — a distributed telescope control system for the Multiple Aperture Spectroscopic Telescope. Several Python services communicate over HTTP (FastAPI), coordinated by a central controller.

## Project Structure

| Project | Role | Runs on |
|---|---|---|
| `MAST_common` | Shared library (submodule in control/spec/gui; sibling clone for units) | — |
| `MAST_control` | Central backend orchestrator | `mast-wis-control` |
| `MAST_spec` | Spectrograph control backend | `mast-wis-spec` |
| `MAST_unit.*` | Per-unit backend (telescope hardware) | Each unit machine (`mast01`…`mast20`) |
| `MAST_gui` | Django web frontend | `mast-wis-control` |

### How each project gets `MAST_common`

**Two mechanisms are in play. The submodule is being phased out.**

| Project | Mechanism | Path |
|---|---|---|
| `MAST_unit.*` | **sibling clone** (flat layout) | `<top>/common/`, beside `<top>/unit/` |
| `MAST_control` | submodule — *to be phased out* | `./common/` |
| `MAST_spec` | submodule — *to be phased out* | `./common/` |
| `MAST_gui` | submodule — *to be phased out* | `./common/` |

#### TODO: retire the submodule in `MAST_control`, `MAST_spec` and `MAST_gui`

`MAST_unit` dropped it on 2026-08-06 (MAST_unit#94). The other three still carry a
gitlink and a `.gitmodules`; do the same there, then delete this section.

Why it went, and what to check before repeating it elsewhere: in the unit the gitlink
turned out to be **resolving nothing**. The service starts with its `src/` as the working
directory and no `PYTHONPATH`, and `import common` was already satisfied by a `mast.pth`
that MAST_provisioning writes into the venv, pointing at the flat top folder. So the
submodule was a second, stale mechanism (its gitlink several merges behind) shadowing the
one actually in use. An *uninitialised* submodule directory is also a live hazard: it is
an empty directory that Python can treat as a namespace-package portion named `common`,
and it makes ruff's first-party classification machine-dependent.

Before removing it from a project, confirm for that project:

1. how `common` is on `sys.path` at **runtime** — a `.pth`, an installed package, or the
   submodule directory itself. Only the first two survive removal. Django's `manage.py`
   and WSGI entry points make this a different question in `MAST_gui` than in the units;
2. that every deployed machine is provisioned that way, not just the dev checkout;
3. what breaks in the repo's own files — the `@common/CLAUDE.md` import at the top of its
   `CLAUDE.md`, `ruff.toml`'s `extend-exclude`, test bootstraps, IDE launch configs.

Keep `known-first-party = ["common"]` in each `ruff.toml`. It matters **more** after the
move, not less: the package then lives outside the repo, where ruff's path-based resolver
cannot classify it at all.

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
`MAST_common` is checked out in several places — `MAST_control/common/`, `MAST_spec/common/`, `MAST_gui/common/`, and, for units, the sibling `<top>/common/`. They are independent checkouts of the same repository, so after changing any file under a `common/`, apply the same change to (or re-sync) the other checkouts so they don't diverge.

The sibling clone is updated with a plain `git pull` in it — there is no gitlink to bump, and the section below does not apply to it.

### Updating the `common/` submodule — use `--remote`

*Applies to `MAST_control`, `MAST_spec` and `MAST_gui` only, and only until the submodule is retired there (see above). Units have no submodule to update.*

Always update with:

```bash
git submodule update --remote
```

A bare `git submodule update` checks out the **commit recorded in the parent's gitlink**, which leaves `common/` on a **detached HEAD** — commits made there belong to no branch and are easy to lose. Each remaining parent's `.gitmodules` sets `branch = master` for its `common` submodule, and `--remote` follows that branch instead.

If a `common/` checkout is already detached, reattach with `git -C <parent>/common checkout master` (verify nothing is stranded first: `git -C <parent>/common branch -a --contains HEAD`).

Note that `--remote` moves the checkout to the branch tip, which will usually be **ahead of the parent's gitlink** — the parent then reports `modified: common (new commits)`. That is a pointer difference, not a file change; resolve it by committing the bumped gitlink in the parent.

## Project-wide LLM guidance

Cross-repo LLM guidance for MAST lives in the **`mast-claude-config`** repo (`github.com/The-MAST-project/mast-claude-config`) — the overarching home for project-wide instructions (shared coding standards, team working-style, global environment facts), deployed into `~/.claude/` by its `setup.sh`. Keep repo-specific guidance in the per-repo `CLAUDE.md`; put genuinely cross-repo guidance there. See `mast-claude-config/CLAUDE.md` for what belongs where.
