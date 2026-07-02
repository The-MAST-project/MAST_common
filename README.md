# MAST Common

Shared library for the MAST (MAsters of Spectra) distributed telescope control
system. Every MAST service embeds this repository as a git submodule:

| Project | Submodule path |
|---|---|
| `MAST_control`, `MAST_spec`, `MAST_gui` | `./common/` |
| `MAST_unit.*` | `./src/common/` |

There is no standalone installation: the code is imported as the `common`
package from within each host project.

## What lives here

- **Configuration** (`config/`) — Pydantic models for the whole system
  configuration plus the `Config` singleton that loads it. Configuration is
  persisted in MongoDB at `mast-ns-control` (database `mast`; collections
  `units`, `sites`, `specs`, `services`, `users`, `groups`), with an optional
  local JSON snapshot (`mast-config-db.json`) taking precedence when present.
  A unit's effective configuration is the `units` collection's `common`
  document deep-merged with the unit-specific document; `Config.set_unit()`
  writes back only the delta from `common`. Fields tagged with
  `json_schema_extra` UI metadata are editable from the GUI by users holding
  the required capability.
- **Component interfaces** (`interfaces/`) — the `Component` ABC and the
  hardware-facing interfaces (imager, guider, mount, …) implemented by the
  services.
- **Models** (`models/`) — Pydantic models shared across services: statuses,
  observation plans, targets, spectrograph assignments.
- **API plumbing** (`api.py`, `canonical.py`, `const.py`) — `ApiClient`
  wrappers and the `CanonicalResponse` envelope returned by all endpoints.
- **Infrastructure** — logging (`mast_logging.py`), notifications, process
  watching, filesystem helpers, safety checks.

## Conventions

- Python 3.11+, Pydantic v2 models at all module interfaces.
- All endpoints return `CanonicalResponse` (`value` on success, `errors` on
  failure).
- Architecture and design rationale is recorded in `DECISIONS.md` (newest
  first).

See `CLAUDE.md` for a fuller structural overview and agent-facing guidance.
