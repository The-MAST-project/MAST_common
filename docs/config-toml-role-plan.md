# Plan: fixed `config.toml` + in-file `machine_role` (remove `MAST_PROJECT` / `MAST_ROLE`)

**Date:** 2026-07-19 (revised 2026-07-20)
**Status:** proposed — for review before any code changes.
**Revisions:**
- 2026-07-20 — field named **`machine_role`**, not `role` (disambiguates from the
  *user* role concept — `UserConfig`/`GroupConfig` capabilities like
  admin/owner/operator); constant renamed `VALID_MACHINE_ROLES`. Pre-config
  logging fallback file is **`mast-STARTUP-log.txt`** (not `mast-unknown_role-log.txt`).
**Scope:** MAST_common (source), MAST_provisioning (writer), the four consumers
(MAST_unit, MAST_control, MAST_gui, MAST_spec), and the two on-site Linux hosts
(mast-ns-control, mast-ns-spec).

**Code baseline (re-verified 2026-07-19):** MAST_common fast-forwarded to
`master` @ `8166547` (was 9 commits behind). Consumers checked at their
integration refs — MAST_unit `main`, MAST_control `master`, MAST_gui `main`,
MAST_spec `master`, MAST_provisioning `main` (local checkout is on the feature
branch `eli/provisioning-v3`, 1 ahead of `origin/main`; these edits target
`main`). The re-check found **three** additional consumers not in the first
sweep — see §3.

---

## 1. Goal

Stop learning a machine's role from an **environment variable** and stop
selecting the config file **by role in its path**. Instead:

- One fixed bootstrap file per machine:
  - Windows: `C:\WIS\config.toml`
  - Linux: `/etc/wis/config.toml`
  - `$MAST_CONFIG` still overrides the path (dev / VM / tests).
- The machine's role becomes a **required `machine_role` field inside that
  file**, validated against `VALID_MACHINE_ROLES = ("unit", "spec", "control")`
  (the values are unchanged). Named `machine_role` (not `role`) to keep it
  distinct from the **user** role concept already in the code
  (`UserConfig`/`GroupConfig` capabilities — admin/owner/operator/…).
- `MAST_PROJECT` is removed everywhere. The separately-proposed `MAST_ROLE`
  (topology epic) is **not** introduced.

### Why

- **Kills the "inject the env var everywhere" problem.** Today every launcher
  (NSSM service, `.bat`, `docker-compose`, manual shell) has to set
  `MAST_PROJECT`, and a Linux host has to persist it too. That is the entire
  friction that made spec/control/gui hard.
- **Makes gui + control on one machine correct by construction.** Both apps on
  mast-ns-control read the *same* `/etc/wis/config.toml` → both get
  `machine_role = "control"`. Two apps on one box can no longer disagree about the
  machine's identity. (Role is a property of the *machine*, not the *app*; gui
  never had a role of its own — it already ran as `MAST_PROJECT=control`.)
- **Single source of truth.** Role now lives beside `site` / `project` /
  `controller_host` / `location` in one file, loaded once by
  `load_local_config()`.

### Explicitly out of scope

- The topology epic (`common-topology-component-overview.md`,
  `common-topology-component-stage-1.md`) and its `MAST_ROLE` /
  `MAST_TOPOLOGY` contract. A "revisit" marker was added to both files
  (2026-07-19); they are **not** reconciled here and must be reviewed
  holistically later.
- Any change to the MongoDB `sites`-doc cross-validation. **Role is not added
  to that cross-check** — a site hosts multiple roles, so role stays TOML-only.

---

## 2. The contract, before vs after

| | Before | After |
|---|---|---|
| File path | `/etc/wis/<role>.toml`, role from `$MAST_PROJECT` | fixed `/etc/wis/config.toml` (`$MAST_CONFIG` overrides) |
| Windows path | `C:\WIS\<role>.toml` | `C:\WIS\config.toml` |
| Role source | `os.getenv("MAST_PROJECT")` | `LocalConfig.machine_role` (TOML field) |
| Env vars | `MAST_PROJECT` required by every process | none |
| Role validity | env checked against `VALID_ROLES` in `_config_file_path` | pydantic field-validator on `LocalConfig.machine_role` |
| Fail-fast on bad/missing | `ConfigError` (env unset / file missing) | `ConfigError` (file missing / `machine_role` missing / invalid) |

---

## 3. Where the role is actually consumed (blast radius)

Re-verified against current integration branches. `MAST_PROJECT` (role) is read
in **three** places in `common` on `master` @ `8166547`:

1. `config/local.py:69` — file-path selection. **Removed** by this change (§4.1).
2. `notifications.py:41` — machine-type label for the notification originator.
   **Redirected** to `local.machine_role` (§4.2). Called lazily at runtime — safe.
3. `mast_logging.py:85` — log file name `mast-<machine_role>-log.txt` (added
   2026-07-19, commit `8166547`). **Redirected** to `local.machine_role`, but with
   an import-time caveat (§4.3) — `init_log()` runs at import, so it must not force
   an eager config load or raise `ConfigError` during import.

Every other consumer (`api.py`, `networking.py`, `dlipowerswitch.py`,
`filer.py`, `models/assignments.py`, `Config.__init__`) already uses
`load_local_config()` for `.domain` / `.mongo_uri` / `.database` / `.site` /
`.location` / `.project` and does not touch role.

Outside `common`, role env-setters / assertions live in launchers and provisioning
(all removed/rewritten — §5, §6): `MAST_unit/service/mast-service.ps1`,
`MAST_common/services/mast-unit/start_mast_unit.bat`, `docker-compose.yml`, and
five provisioning scripts (`provide-config-bootstrap.ps1`,
`verify-config-bootstrap.ps1`, `provide-mast.ps1`, `provide-mast-validation.ps1`,
`validate_mastrometry.py`).

---

## 4. Phase 1 — MAST_common (source of truth)

### 4.1 `config/local.py`

**(a) `LocalConfig` — add the `machine_role` field + validator.**

```python
from pydantic import BaseModel, ValidationError, field_validator

# machine role (distinct from the USER role in UserConfig/GroupConfig)
VALID_MACHINE_ROLES = ("unit", "spec", "control")


class LocalConfig(BaseModel):
    site: str
    project: str
    machine_role: str    # NEW: machine role; was the MAST_PROJECT env var
    controller_host: str
    database: str
    domain: str
    location: Location
    mongo_port: int = 27017

    @field_validator("machine_role")
    @classmethod
    def _validate_machine_role(cls, v: str) -> str:
        if v not in VALID_MACHINE_ROLES:
            raise ValueError(
                f"machine_role={v!r} is invalid; expected one of "
                f"{', '.join(VALID_MACHINE_ROLES)}"
            )
        return v

    # mongo_uri / data_root unchanged
```

Notes:
- `machine_role: str` has **no default** → an absent `machine_role` key fails
  validation, same fail-fast guarantee the old env-unset check gave.
- The `ValueError` raised inside `_validate_machine_role` is wrapped by pydantic
  into a `ValidationError`, which `load_local_config` already catches and
  re-raises as `ConfigError` with the exact field diff (see 4.1(c)). No new error
  plumbing.

**(b) `_config_file_path()` — fixed path, no role logic.**

```python
def _config_file_path() -> str:
    """Locate the bootstrap TOML file.

    Resolution order:
      1. `$MAST_CONFIG` — explicit override (dev / VM / tests).
      2. Fixed default: `C:\\WIS\\config.toml` (Windows) or
         `/etc/wis/config.toml` (*nix). The machine's role is a field inside
         the file (`LocalConfig.machine_role`), not part of the path.
    """
    override = os.getenv("MAST_CONFIG")
    if override:
        return override
    if platform.system() == "Windows":
        return r"C:\WIS\config.toml"
    return "/etc/wis/config.toml"
```

Removed: the entire `MAST_PROJECT` read + `VALID_ROLES` membership check that
lived here (it moves onto the field validator as `VALID_MACHINE_ROLES`).

**(c) `load_local_config()` — unchanged.** It already:
- raises `ConfigError` if the file does not exist,
- reads `utf-8-sig` (tolerates a PowerShell BOM),
- catches `TOMLDecodeError` → `ConfigError` with line/column,
- catches `ValidationError` → `ConfigError` with the per-field diff (this now
  also surfaces a missing/invalid `machine_role`).

### 4.2 `notifications.py` (the one behavioral edit)

```python
local = load_local_config()
role = local.machine_role                 # was: os.getenv("MAST_PROJECT")
machine_type = {"unit": "unit", "spec": "spec", "control": "controller"}.get(
    role, "unknown-machine-type"
)
```

- Drop the now-unused `import os` if nothing else in the module needs it.
- Fix the docstring at line 36 ("machine type comes from the `MAST_PROJECT`
  role" → "from the config file's `machine_role` field").
- Because `machine_role` is now validated at load time, the
  `"unknown-machine-type"` fallback becomes unreachable — keep it as a defensive
  default (cheap), but it no longer silently mislabels a machine, which was the
  old failure mode.

### 4.3 `mast_logging.py` (import-time consumer — needs care)

Current (commit `8166547`):

```python
role = os.getenv("MAST_PROJECT", "unknown_role")
file_name = f"mast-{role}-log.txt"
```

`init_log()` is invoked at **import time** (e.g. `config/__init__.py:31`), so it
must not force an eager `load_local_config()` (which would read/validate the file
during import and could raise `ConfigError` before the app's startup gate — the
exact anti-pattern `notifications.py` documents avoiding). Fix: lazy import +
guarded load, falling back to a dedicated **`STARTUP`** marker:

```python
def init_log(logger_, level=None):
    ...
    try:
        from common.config.local import load_local_config
        role = load_local_config().machine_role
    except Exception:
        role = "STARTUP"               # logging must never break import/startup
    file_name = f"mast-{role}-log.txt"
    ...
```

- **Deliberate broad `except`** (justified, call-site-silenced): logging setup
  must not crash the process, and the config may legitimately not be loadable yet
  at import. The real fail-fast still happens at the `Config()` startup gate.
  `load_local_config` is `lru_cache`d, so once the app has loaded config, the log
  name resolves to the true role on any later `init_log`.
- **Why `mast-STARTUP-log.txt` is the right fallback** (not `unknown_role`):
  `load_local_config()` only reads the *file* (no Mongo), so in normal operation
  the role resolves even during import-time `init_log()` calls → `mast-unit-log.txt`
  etc. as expected. The fallback fires *only* when the config genuinely can't be
  read yet, so `mast-STARTUP-log.txt` precisely means "log lines from a process
  that hadn't/couldn't load its config" — a useful signal, not an error-looking
  value. (Caveat worth knowing: a logger whose `init_log()` ran before config was
  available keeps its file name for the process lifetime; in practice that only
  happens in the genuine no-config case, which then fails fast at `Config()`.)

### 4.4 `config/local.toml.example`

- Add a `machine_role` key with a comment enumerating valid values:
  ```toml
  machine_role = "unit"   # machine role: one of "unit", "spec", "control"
  ```
- Rewrite the header comment: fixed deploy path `C:\WIS\config.toml` /
  `/etc/wis/config.toml`, `$MAST_CONFIG` override, **no `MAST_PROJECT`**.

### 4.5 MAST_common docs

- `CLAUDE.md` lines 31 & 90 — rewrite the config-contract description (fixed
  `config.toml`, in-file `machine_role`, no env var).
- `DECISIONS.md` — **append** a new dated entry at the top (date from `date`):
  *why* (remove env-var injection; gui+control same-box consistency; disambiguate
  from user role) → *what* (fixed `config.toml`, required `machine_role` field,
  `MAST_PROJECT` removed,
  `MAST_ROLE` never introduced) → *implications* (breaking bootstrap change;
  supersedes the 2026-07-xx external-config env contract; topology epic deferred).
- Topology docs — **marker already added** (2026-07-19); no further change.

---

## 5. Phase 2 — MAST_provisioning (the write side)

### 5.1 `server/providers/config-bootstrap/provide-config-bootstrap.ps1`

Current behavior: copies `sites/<Site>.toml` **verbatim** to
`C:\WIS\<Role>.toml`, then sets `MAST_PROJECT` machine-wide.

Changes:
1. **Target path** → `C:\WIS\config.toml` (was `C:\WIS\<Role>.toml`).
2. **Inject the machine role into the file.** The site profile is per-site
   (shared by all machine types at a site), so it carries no role. Build
   `config.toml` as `machine_role = "<Role>"` **prepended** to the site-profile
   body.
   - ⚠️ **TOML-correctness detail:** `machine_role` is a top-level key, so it MUST
     appear *before* the first table header. The site profiles end with a
     `[location]` table; appending it at the end would fold it into `[location]`.
     Prepend it (as the first content line) instead.
   - Suggested implementation: read the profile text, write
     `"machine_role = \"$Role\"`n"` followed by the original content, to
     `C:\WIS\config.toml` (keep the UTF-8 write; `load_local_config` strips a BOM).
3. **Delete** the env-var block (lines 45–50):
   `[Environment]::SetEnvironmentVariable('MAST_PROJECT', $Role, 'Machine')` and
   `$env:MAST_PROJECT = $Role`.
4. Update the header comment (lines 6–7) and the started/wrote log lines.

`${Role}` stays a provider param (default `unit`) — it now names the value
written into the file, not an env var or a filename.

### 5.2 Other provisioning edits

- `server/providers/mast/provide-mast.ps1:432-436` — **remove** the NSSM
  `AppEnvironmentExtra 'MAST_PROJECT=unit'` (no longer needed).
- `server/providers/config-bootstrap/verify-config-bootstrap.ps1:36-42` —
  **rewrite the verification.** It currently asserts the machine env var
  `MAST_PROJECT` equals the role; change it to parse `C:\WIS\config.toml` and
  assert `machine_role = "<Role>"` is present and correct. (The file-exists +
  required-keys checks stay; only the env-var assertion is replaced by a
  `machine_role`-field assertion.)
- `server/providers/config-bootstrap/module.json` — rewrite `description`
  (writes `C:\WIS\config.toml`, injects `role`, no env var).
- `server/providers/mast-validation/provide-mast-validation.ps1:79-80` —
  **remove** `$env:MAST_PROJECT = 'unit'`; the validation process should rely on
  the provisioned `C:\WIS\config.toml` (or `MAST_CONFIG` → a fixture).
- `server/providers/mast-validation/validate_mastrometry.py:81` — replace
  `os.environ.setdefault("MAST_PROJECT", "unit")` with either pointing
  `MAST_CONFIG` at a fixture `config.toml` (carrying `machine_role = "unit"`) or
  asserting the provisioned `C:\WIS\config.toml` exists. (This harness currently
  leans on the env var to satisfy `load_local_config`.)
- `README.md:117` — update the provider-table row.
- `CLAUDE.md:190-195` — rewrite the "Unit config" section.
- `DECISIONS.md` — **append** a new dated entry at the top; the existing
  2026-06-29 `config-bootstrap` entry stays untouched.

---

## 6. Phase 3 — Consumers (unit, control, gui, spec)

1. **Bump the `common` submodule** in MAST_unit, MAST_control, MAST_gui,
   MAST_spec to the merged MAST_common commit. (control's & spec's checkouts are
   currently *pre-epic* stale — this pull-forward also lands `config/local.py`
   and removes the old `config_toml.py` there.)
2. **Remove stray env-setters (app-level, outside vendored `common`):**
   - `MAST_unit/service/mast-service.ps1:83` — the NSSM
     `AppEnvironmentExtra 'MAST_PROJECT=unit'` (a *second* env-setter, separate
     from provisioning's `provide-mast.ps1`). Remove.
   - `MAST_common/services/mast-unit/start_mast_unit.bat:5` (`set MAST_PROJECT=unit`)
     — **source lives in MAST_common** and is vendored into the consumers; fix at
     the source, it propagates on the submodule bump.
   - Top-level `docker-compose.yml` — the three `MAST_PROJECT: control` service
     envs (lines 96, 115, 135). Replace with a mounted/baked `config.toml`
     containing `machine_role = "control"` (and `MAST_CONFIG` pointing at it, or
     the default `/etc/wis/config.toml` mount).
   - `MAST_unit/.idea/runConfigurations/apply_obstruction.xml:9` — the
     `<env name="MAST_PROJECT" value="unit"/>` IDE run-config entry (dev-only;
     drop it, or point that run config at a `MAST_CONFIG` fixture).
3. **Doc refs (each consumer's `CLAUDE.md`):** MAST_control:8, MAST_spec:10,
   MAST_unit:11 all show `MAST_PROJECT=<role> python app.py` — update to the
   `config.toml` contract. MAST_unit `DECISIONS.md:97` is historical (append-only);
   leave it and let the new MAST_common/provisioning entries supersede.
4. Each consumer vendors `common/CLAUDE.md` + `common/DECISIONS.md`; they update
   with the submodule bump.

---

## 7. Phase 4 — Linux deployment (mast-ns-control, mast-ns-spec)

No code — placement only, now with **no env var to persist**:

- `mast-ns-control`: write `/etc/wis/config.toml` with `machine_role = "control"`,
  `site = "ns"`. Serves both control **and** gui.
- `mast-ns-spec`: write `/etc/wis/config.toml` with `machine_role = "spec"`.
- Values (`project`, `controller_host`, `location`) must match the `ns` `sites`
  document, or `Config()` fails the cross-check at startup.
  **TODO before writing:** pull the live `ns` sites doc read-only (mongosh
  on-host) to confirm the exact values.
- Delivery mechanism (still open, but smaller now that there is no env var):
  short idempotent install step documented in the vault
  `Installation Notes 2026-06-15.md`, vs. a Linux bootstrap role in
  provisioning. **Lean: document the two-box step.**

---

## 8. Validation methods

### 8.1 In-code validation (fail-fast, already wired)

- **Required-field validation:** `LocalConfig.machine_role` has no default →
  pydantic raises `ValidationError` (→ `ConfigError`) if the key is absent.
- **Enum validation:** `_validate_machine_role` rejects any value outside
  `VALID_MACHINE_ROLES` with an explicit message → `ValidationError` → `ConfigError`.
- **File-presence validation:** `load_local_config` raises `ConfigError`
  ("configuration file '…' does not exist") before parsing.
- **Parse validation:** `tomllib.TOMLDecodeError` → `ConfigError` with
  line/column.
- **DB cross-validation (unchanged):** `Config._validate_local_identity`
  compares `project`, `controller_host`, `location.{latitude,longitude,
  elevation}` against the `sites` doc and raises `ConfigError` with the exact
  diff. Role is deliberately excluded.
- **Startup gate (unchanged):** apps build `Config()` inside
  `try/except ConfigError` → log the reason → exit non-zero. No app runs on an
  invalid file.

### 8.2 Unit tests (MAST_common, pytest)

Add to the config test module (drive `load_local_config` via `MAST_CONFIG`
pointing at temp fixtures; clear the `lru_cache` between cases):

```python
def test_valid_config_loads(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(VALID_TOML)                 # includes machine_role = "control"
    monkeypatch.setenv("MAST_CONFIG", str(cfg))
    load_local_config.cache_clear()
    assert load_local_config().machine_role == "control"

def test_missing_machine_role_field_fails(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(VALID_TOML_WITHOUT_MACHINE_ROLE)
    monkeypatch.setenv("MAST_CONFIG", str(cfg))
    load_local_config.cache_clear()
    with pytest.raises(ConfigError) as e:
        load_local_config()
    assert "machine_role" in str(e.value)

def test_invalid_machine_role_value_fails(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(VALID_TOML.replace('machine_role = "control"', 'machine_role = "gui"'))
    monkeypatch.setenv("MAST_CONFIG", str(cfg))
    load_local_config.cache_clear()
    with pytest.raises(ConfigError) as e:
        load_local_config()
    assert "expected one of" in str(e.value)

def test_fixed_default_path_used_when_no_env(monkeypatch):
    monkeypatch.delenv("MAST_CONFIG", raising=False)
    monkeypatch.delenv("MAST_PROJECT", raising=False)   # must be irrelevant now
    # platform-branch: assert _config_file_path() endswith "config.toml"
    assert _config_file_path().endswith("config.toml")

def test_missing_file_fails(monkeypatch):
    monkeypatch.setenv("MAST_CONFIG", "/nonexistent/config.toml")
    load_local_config.cache_clear()
    with pytest.raises(ConfigError):
        load_local_config()

def test_notification_machine_type_from_role(tmp_path, monkeypatch):
    # role = "control" -> initiator.type == "controller"
    ...

def test_log_name_uses_machine_role_and_survives_missing_config(monkeypatch):
    # With a valid config -> "mast-<machine_role>-log.txt".
    # With MAST_CONFIG pointing nowhere -> init_log() does NOT raise at import,
    # and falls back to "mast-STARTUP-log.txt".
    ...
```

Regression guard: grep the test suite (and CI) to assert **no test sets
`MAST_PROJECT`** anymore.

### 8.3 End-to-end validation (extends the 2026-07-09 VM matrix)

Run each case in a fresh process against a MongoDB the guest can reach
(`Config()` in `try/except ConfigError`, mirroring the unit startup gate):

| Case | Expected |
|------|----------|
| Valid `config.toml` (with `machine_role`) matching the DB, FQDN Mongo connect + cross-validate | loads, exit 0 |
| `config.toml` missing | ConfigError "does not exist", exit 1 |
| `machine_role` key absent | ConfigError (validation), exit 1 |
| `machine_role = "gui"` (invalid) | ConfigError "expected one of …", exit 1 |
| Malformed TOML | ConfigError parse error w/ line/col, exit 1 |
| Drift vs DB `sites` doc (e.g. elevation 999 ≠ 400) | ConfigError exact diff, exit 1 |
| `MAST_PROJECT` set but no file | still ConfigError "does not exist" (env now inert) |

The last row is the meaningful proof that the env var is truly dead.

### 8.4 Provisioning-side validation

- Re-provision one unit end-to-end; assert `C:\WIS\config.toml` exists, contains
  `machine_role = "unit"` as a top-level key **before** `[location]`, and that
  `MAST_PROJECT` is **not** set machine-wide afterward.
- `validate_mastrometry.py` runs green using the file (not the env var).
- Start the `mast-unit` NSSM service and confirm it reaches Mongo and passes the
  cross-check.

### 8.5 Per-machine smoke checklist (Linux)

- `mast-ns-control`: with `/etc/wis/config.toml` (`machine_role = "control"`)
  present, launch the control app **and** the gui — both load, both report machine
  type `controller`, both connect to Mongo. Remove the file → both fail fast with
  the same clear reason.
- `mast-ns-spec`: same, with `machine_role = "spec"`.

---

## 9. Rollout order & safety

1. **MAST_common PR** — code (4.1, 4.2), example (4.3), docs (4.4), tests (8.2).
   Merge to `master`.
2. **MAST_provisioning PR** — writer (5.1), other edits (5.2), docs. Merge.
3. **Consumer submodule bumps** — one PR each for unit/control/gui/spec (6),
   plus the `.bat` / `docker-compose.yml` env removals.
4. **Linux deployment** (7) + re-provision/verify one unit (8.4) + smoke (8.5).

**Breaking-change note:** a machine on new code with an old file (no
`machine_role`, old `<role>.toml` name) fails fast at startup — intended, loud.
Units are fixed on next provision; the two Linux boxes in Phase 4.

**Rollback:** revert the MAST_common submodule bump on a consumer to fall back to
the previous `common` (which still reads the old path/env). Because the change is
localized to `local.py` + `notifications.py` + `mast_logging.py` + the example, a
revert is clean.

---

## 10. Files-touched checklist (for review)

**MAST_common** (`master` @ `8166547`)
- [ ] `config/local.py` — `machine_role` field + validator; fixed-path `_config_file_path`
- [ ] `notifications.py` — role from `local.machine_role`; docstring; drop `os` if unused
- [ ] `mast_logging.py` (85) — role from guarded `load_local_config().machine_role`; `STARTUP` fallback
- [ ] `services/mast-unit/start_mast_unit.bat` (5) — remove `set MAST_PROJECT`
- [ ] `config/local.toml.example` — add `role`; rewrite header
- [ ] `CLAUDE.md` (31, 90); `DECISIONS.md` (append)
- [ ] config tests + logging test (8.2)

**MAST_provisioning** (`main`)
- [ ] `server/providers/config-bootstrap/provide-config-bootstrap.ps1`
- [ ] `server/providers/config-bootstrap/verify-config-bootstrap.ps1` (36–42)
- [ ] `server/providers/mast/provide-mast.ps1` (432–436)
- [ ] `server/providers/mast-validation/provide-mast-validation.ps1` (79–80)
- [ ] `server/providers/mast-validation/validate_mastrometry.py` (81)
- [ ] `server/providers/config-bootstrap/module.json`
- [ ] `README.md` (117); `CLAUDE.md` (163–168); `DECISIONS.md` (append)

**Consumers**
- [ ] `common` submodule bump ×4 (unit, control, gui, spec)
- [ ] `MAST_unit/service/mast-service.ps1` (83) — remove NSSM `MAST_PROJECT`
- [ ] `MAST_unit/.idea/runConfigurations/apply_obstruction.xml` (9) — dev run config
- [ ] `CLAUDE.md` doc refs — control (8), spec (10), unit (11)
- [ ] top-level `docker-compose.yml` (96, 115, 135)

**Deployment / vault**
- [ ] `/etc/wis/config.toml` on mast-ns-control (`machine_role = "control"`)
- [ ] `/etc/wis/config.toml` on mast-ns-spec (`machine_role = "spec"`)
- [ ] vault `Installation Notes 2026-06-15.md`; follow-up note superseding the
      2026-07-09 dated-record

**Topology (marker only — done 2026-07-19)**
- [x] `common-topology-component-overview.md` — revisit marker
- [x] `common-topology-component-stage-1.md` — revisit marker
