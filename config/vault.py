"""Credentials that must never be tracked in git.

One file for the fleet, on the share, beside the per-machine product trees rather
than inside one -- see MAST_common#60. `Filer.share_root` is what locates it, so the
Windows drive letter becoming a UNC (MAST_common#26) changes nothing here.

Three properties this deliberately does NOT share with the rest of `config`:

* **Not part of any dumped model.** `Config`'s contents are `model_dump()`ed and
  printed as a matter of course -- `get_specs()`, `get_sites()`, `unit_conf.model_dump()`,
  and a `__main__` that prints the whole thing as JSON. A secret reachable from one of
  those reaches stdout the first time that path runs. The vault hangs off `Config` as a
  plain attribute instead, and every secret is a `SecretStr`, which masks under `repr`,
  `str`, f-strings, `model_dump` and `model_dump_json` and yields only to an explicit
  `get_secret_value()`.
* **Loaded lazily.** `Config` already depends on MongoDB at startup; loading this in its
  constructor would add the share as a second startup dependency, and the share was
  unreachable for eighteen minutes on 2026-08-11.
* **Never fatal.** Everything else in `config` raises `ConfigError` and the application
  is expected to stop. A missing or malformed vault degrades whatever needed the
  credential -- name resolution falls back to Sesame alone -- and must not stop a
  telescope.
"""

import tomllib

from pydantic import BaseModel, SecretStr

from common.filer import Filer
from common.mast_logging import get_logger

logger = get_logger(__name__)

#: Name of the vault file within `Filer().share_root`.
VAULT_FILE_NAME = "vault.toml"


class TnsVault(BaseModel):
    """Transient Name Server bot credentials.

    Without an api_key, TNS-shaped names (AT/SN + year) cannot be resolved to the
    transient's own discovery position -- and Sesame may answer such a name with the
    HOST GALAXY's position instead, which points the telescope somewhere plausible and
    wrong. See MAST_common#60.
    """

    api_key: SecretStr | None = None


class MastCredentials(BaseModel):
    """The MAST service account."""

    user: str | None = None
    password: SecretStr | None = None


class VaultConfig(BaseModel):
    """The vault's contents. Every section is optional: a vault missing a section is a
    capability that degrades, not a configuration error."""

    tns: TnsVault = TnsVault()
    mast_credentials: MastCredentials = MastCredentials()


def vault_path() -> str:
    """Where the vault lives: `<share root>/vault.toml`.

    `share_root`, not `shared.root` -- the latter is this machine's product tree, so a
    vault there would be one file per machine inside the tree that gets bulk-copied
    during cleanups. One file for the fleet is the whole point.
    """
    from pathlib import PurePath

    return str(PurePath(Filer().share_root.root) / VAULT_FILE_NAME)


def load_vault(path: str | None = None) -> VaultConfig:
    """Read the vault, or return an empty one.

    Never raises. Every failure -- absent file, unreachable share, malformed TOML,
    unexpected shape -- is logged and yields an empty `VaultConfig`, so a caller that
    needs a credential finds it missing and degrades, rather than the process dying at
    a point where nothing has gone wrong with the telescope.
    """
    target = path or vault_path()
    try:
        with open(target, "rb") as fp:
            data = tomllib.load(fp)
    except FileNotFoundError:
        logger.warning(f"vault: '{target}' does not exist; continuing without credentials")
        return VaultConfig()
    except OSError as e:
        # A dead share reaches here, which is why this is not fatal.
        logger.warning(f"vault: could not read '{target}' ({e}); continuing without credentials")
        return VaultConfig()
    except tomllib.TOMLDecodeError as e:
        logger.error(f"vault: '{target}' is not valid TOML ({e}); continuing without credentials")
        return VaultConfig()

    try:
        return VaultConfig(**data)
    except Exception as e:  # noqa: BLE001 -- pydantic raises ValidationError, but a
        # malformed vault must degrade like every other failure here, not propagate.
        logger.error(f"vault: '{target}' has an unexpected shape ({e}); continuing without credentials")
        return VaultConfig()
