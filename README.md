# MAST_common

Shared Python library for the MAST telescope control system. Submoduled into every
MAST_* project and imported as `common.*`.

## Overview

Provides cross-cutting modules used by every MAST service:

- `config/` — `Config` singleton (MongoDB + TOML)
- `models/` — Pydantic domain types (plans, statuses, instruments, …)
- `interfaces/` — `Component` ABC and related contracts
- `tasks/` — shared task definitions
- `api.py`, `canonical.py`, `notifications.py`, `mast_logging.py`, …
- **`build_report.py`** — cross-module build / version report collector (see below)
- **`build_report_api.py`** — drop-in FastAPI router that exposes `/build-report`

## Build report

Every MAST FastAPI service exposes a uniform `/build-report` endpoint that returns
the git identity of every MAST repo on the host plus the actually-loaded
MAST_common version. The goal is to make partial / incomplete deployments
visible — e.g. when one repo has been updated and another has not, or when the
MAST_common submodule SHA pinned by a parent repo is bypassed by a system-wide
install.

### Library API

```python
from pathlib import Path
from common.build_report import collect_build_report

report = collect_build_report(Path("/opt/MAST"))   # workspace containing MAST_* repos
print(report.model_dump_json(indent=2))
```

`BuildReport` fields:

- `host`, `generated_at`, `workspace_root`
- `repos: list[RepoReport]` — per repo: `branch`, `head_sha`, `head_describe`,
  `dirty`, `dirty_summary`, `submodules`, `error?`
- `submodules: list[SubmoduleReport]` — `path`, `recorded_sha`, `checked_out_sha`,
  `matches` (False signals submodule drift)
- `common_package: CommonPackageReport` — `installed_version` from
  `importlib.metadata`, plus `installed_path` from the actually-imported
  `common.__file__`
- `common_sha_consistency: list[str]` — distinct MAST_common SHAs referenced
  across all submodule pins. More than one entry = drift.

### FastAPI integration

In any FastAPI app:

```python
from pathlib import Path
from common.build_report_api import make_build_report_router

WORKSPACE_ROOT = Path(__file__).resolve().parents[N]   # depth depends on layout
app.include_router(make_build_report_router(WORKSPACE_ROOT))
```

This wires `GET /build-report` returning the same `BuildReport` schema as the
library call.

### CLI

The workspace root ships a `build_report.py` CLI that prints a human-readable
table for ops use without needing any service to be running:

```bash
python build_report.py              # tabular text
python build_report.py --json       # JSON dump
```

## Setup

MAST_common is consumed two ways depending on the parent:

- As a **git submodule** at `common/` (or `src/common/` in `MAST_unit*`), checked
  out at the SHA pinned by the parent repo.
- As an **editable install** (`pip install -e ../MAST_common`) in stand-alone
  projects like MAST_scheduler.

The build report reports both the pinned SHA (per parent) and the path of the
actually-loaded `common` package so the two can be cross-checked.

## Branches

`master` is the active branch. `main` exists but is essentially empty and should
not be used; see `DECISIONS.md`.
