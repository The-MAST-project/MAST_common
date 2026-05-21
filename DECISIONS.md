# DECISIONS

## [2026-05-21] Add cross-module build / version report

**Why**

The MAST ecosystem had no way to ask "what is actually deployed across the
system, and is it consistent?". Incomplete deployments — especially MAST_common
drift, where the submodule SHA pinned by a parent repo is bypassed by the
system-wide install — were invisible until something broke at runtime. The most
acute symptom was between `MAST_unit.2024-12-12` (operational/hardware-alignment
target) and `MAST_common`, but the same risk applies to every parent repo
(MAST_control, MAST_gui, MAST_spec, MAST_unit, MAST_unit.2024-12-12).

**What**

- New module `common.build_report`: a single `collect_build_report(workspace_root)`
  function plus Pydantic `BuildReport` schema. Walks `workspace_root` for sibling
  `MAST_*` git repos and assembles branch / HEAD SHA / `git describe` / dirty
  flag / submodule pin-vs-checkout / loaded-MAST_common version-and-path. Pure
  introspection — no remote execution, no SSH.
- New module `common.build_report_api`: a `make_build_report_router(workspace_root)`
  factory that returns a `FastAPI` router with a single `GET /build-report`
  endpoint returning `BuildReport`. Each MAST FastAPI service includes it with
  one line.
- New CLI `MAST/build_report.py` in the workspace root that calls the same
  collector and prints a human-readable table (plus a `--json` flag).

**Implications**

- Every MAST FastAPI service exposes a uniform `/build-report` endpoint with an
  identical JSON schema, so an aggregator can fan out across hosts (deferred —
  not part of this change).
- No new runtime dependencies; everything uses `subprocess`, `pydantic`, and
  `importlib.metadata`, all of which were already present.
- The report is read-only and best-effort: per-repo git errors are captured in
  the response rather than raising, so a single bad repo never 500s the endpoint.
- The endpoint reflects **disk state at call time**, not the state the running
  process loaded at startup. The "common package loaded by this process" path
  is still included, so a human can compare it against the on-disk SHA — but
  there is no automatic restart-required flag.
- A follow-up change is needed in each parent repo: bump the `common/` (or
  `src/common/`) submodule pointer to a MAST_common SHA that includes
  `build_report.py` / `build_report_api.py`. Until that lands, the
  `from common.build_report_api import …` imports in parent apps will fail.

## [2026-05-21] `master` is the canonical branch, not `main`

**Why**

MAST_common has two long-lived branches on its remotes: `master` carries all
the actual code; `main` consists only of an "Initial commit" plus a follow-up
"???" commit that removed everything. Confusingly, `origin/HEAD` points at
`main`, so a naive `git checkout` of the default branch lands on the empty
tree. The same situation exists in MAST_control. This is itself an instance of
the partial-deployment problem the build report addresses.

**What**

Treat `master` as the canonical branch for MAST_common (and MAST_control). New
feature branches should be created from `master`, not from `main`. Do not
attempt to "fix" `main` by force-pushing over it without explicit
coordination — the empty branch may be referenced by tooling.

**Implications**

- README now states the branch convention explicitly so newcomers don't waste
  time on the empty `main`.
- `origin/HEAD` should eventually be re-pointed at `master`, but that is a
  remote/admin change tracked separately.
