# MAST_common — Shared Library

Shared Python library for the MAST (MAsters of Spectra) telescope control system. Consumed as a dependency by `MAST_control`, `MAST_spec`, `MAST_unit`, and `MAST_gui`.

## Requirements

- Python 3.12+

## Installation

Install in editable mode from the project root so all MAST projects pick it up via normal package imports:

```bash
pip install -e /path/to/MAST_common
```

Or, from within the `MAST_common` directory:

```bash
pip install -e .
```

## Key Modules

| Module | Purpose |
|--------|---------|
| `config/` | Singleton `Config` — loads site/unit/service config from MongoDB or local JSON |
| `hostname.py` | `get_hostname()` — respects `MAST_VIRTUAL_HOSTNAME` for dev environments |
| `activities.py` | `Activities` base class — bitflag activity tracking with timing |
| `api.py` | `ApiClient`, `UnitApi`, `SpecApi`, `ControllerApi` — typed HTTP wrappers |
| `notifications.py` | WebSocket-based UI update notifications |
| `models/plans.py` | `Plan` model and `Planner` singleton — file-based plan state machine |
| `mast_logging.py` | `init_log()` — rotating daily log handler with Rich console output |
| `filer.py` | `Filer` — cross-platform path abstraction (local / shared / RAM storage) |

## Development Hostname Override

To simulate a specific machine role on a dev machine:

```bash
export MAST_VIRTUAL_HOSTNAME=mast-wis-control
```

`get_hostname()` will return this value instead of `socket.gethostname()`.
