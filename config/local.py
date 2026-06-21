import os
import platform
import tomllib
from functools import lru_cache

from pydantic import BaseModel, ValidationError

from .site import Location

VALID_ROLES = ("unit", "spec", "control")


class ConfigError(Exception):
    """Raised when the local bootstrap configuration is missing or invalid.

    Carries a detailed, human-readable reason suitable for logging right before
    the application shuts down.
    """


class LocalConfig(BaseModel):
    """The per-machine bootstrap configuration, read from a local TOML file.

    This is the single source of truth for the machine's identity and for how
    to reach the configuration database. It deliberately duplicates a few fields
    (`project`, `controller_host`, `location`) that also live in the MongoDB
    `sites` document; `Config` cross-checks them at startup (see
    `Config._validate_local_identity`) so the two sources can never drift
    silently.
    """

    site: str
    project: str
    controller_host: str  # also the MongoDB host (controller == DB machine)
    database: str
    domain: str  # DNS domain, e.g. "weizmann.ac.il" — the single source of truth
    location: Location
    mongo_port: int = 27017

    @property
    def mongo_uri(self) -> str:
        return f"mongodb://{self.controller_host}:{self.mongo_port}"

    @property
    def data_root(self) -> str:
        """Top-level data folder, dictated by the project name (e.g. C:/MAST)."""
        if platform.system() == "Windows":
            return f"C:/{self.project.upper()}"
        return f"/var/{self.project.lower()}"


def _config_file_path() -> str:
    """Locate the bootstrap TOML file.

    Resolution order:
      1. `$MAST_CONFIG` — explicit override (dev / VM / tests).
      2. Role-based default: `C:\\WIS\\<role>.toml` (Windows) or
         `/etc/wis/<role>.toml` (*nix), where `<role>` is `$MAST_PROJECT`
         (one of 'unit', 'spec', 'control').
    """
    override = os.getenv("MAST_CONFIG")
    if override:
        return override

    role = os.getenv("MAST_PROJECT")
    if not role:
        raise ConfigError(
            "MAST_PROJECT environment variable is not set (expected one of "
            f"{', '.join(VALID_ROLES)}), and MAST_CONFIG is not set either."
        )
    if role not in VALID_ROLES:
        raise ConfigError(
            f"MAST_PROJECT='{role}' is invalid; expected one of {', '.join(VALID_ROLES)}."
        )

    if platform.system() == "Windows":
        return f"C:/WIS/{role}.toml"
    return f"/etc/wis/{role}.toml"


@lru_cache(maxsize=1)
def load_local_config() -> "LocalConfig":
    """Load and validate the per-machine bootstrap configuration (TOML only).

    Cached, so the file is read once per process. Does NOT touch MongoDB, so it
    stays cheap for callers that only need a bootstrap value (e.g. the DNS
    `domain`). Raises `ConfigError` with a detailed reason on any problem.
    """
    path = _config_file_path()
    if not os.path.exists(path):
        raise ConfigError(
            f"configuration file '{path}' does not exist "
            "(set MAST_CONFIG to override the location)."
        )

    try:
        # Read as utf-8-sig so a leading BOM is stripped: PowerShell's
        # `Set-Content -Encoding utf8` (used by provisioning) writes a UTF-8 BOM,
        # which tomllib otherwise rejects ("Invalid statement" at line 1).
        with open(path, encoding="utf-8-sig") as fp:
            raw = tomllib.loads(fp.read())
    except (OSError, tomllib.TOMLDecodeError) as ex:
        raise ConfigError(
            f"cannot read/parse configuration file '{path}': {ex}"
        ) from ex

    try:
        return LocalConfig(**raw)
    except ValidationError as ex:
        details = "\n".join(
            f"  - {'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in ex.errors()
        )
        raise ConfigError(f"invalid configuration in '{path}':\n{details}") from ex
