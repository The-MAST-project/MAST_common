"""The vault: credentials from the share, read once, and never fatal.

Three properties matter more than the parsing, and each is here because getting it
wrong is silent rather than loud:

* secrets must not survive a `model_dump` -- `Config`'s contents are dumped and printed
  as a matter of course, including a `__main__` that prints the lot as JSON;
* a missing or broken vault must degrade, not raise -- everything else in `config`
  raises `ConfigError` and stops the application, which is wrong for a credential a
  telescope does not need in order to point;
* it must sit at the share root, not in this machine's product tree.
"""

from __future__ import annotations

from pathlib import PurePath

import pytest

from common.config.vault import VAULT_FILE_NAME, MastCredentials, TnsVault, VaultConfig, load_vault, vault_path
from common.filer import Filer


class TestWhereItLives:
    def test_it_sits_at_the_share_root(self):
        assert PurePath(vault_path()).parent == PurePath(Filer().share_root.root)
        assert PurePath(vault_path()).name == VAULT_FILE_NAME

    def test_it_is_not_in_a_per_machine_product_tree(self):
        """`shared.root` is per-machine on Windows and gets bulk-copied during cleanups;
        a secret there would be twenty files and would travel with the data.

        Windows only: on Linux `shared.root` IS the share root, so there is no
        per-machine tree for the vault to be wrongly inside, and the check is vacuous.
        """
        import platform

        if platform.system() != "Windows":
            pytest.skip("no per-machine product tree on this platform")
        assert not str(vault_path()).startswith(str(PurePath(Filer().shared.root)))

    def test_it_does_not_carry_the_hostname(self):
        import socket

        assert socket.gethostname().lower() not in vault_path().lower()


class TestSecretsAreMasked:
    """`Config` is dumped and printed routinely. A secret reachable from one of those
    reaches stdout the first time that path runs."""

    @pytest.fixture
    def loaded(self, tmp_path):
        p = tmp_path / "vault.toml"
        p.write_text('[tns]\napi_key = "tns-bot-key"\n\n[mast_credentials]\nuser = "mast"\npassword = "hunter2"\n')
        return load_vault(str(p))

    @pytest.mark.parametrize("secret", ["tns-bot-key", "hunter2"])
    def test_no_representation_leaks_the_value(self, loaded, secret):
        for rendering in (repr(loaded), str(loaded), f"{loaded}", str(loaded.model_dump()), loaded.model_dump_json()):
            assert secret not in rendering, f"leaked through {rendering[:60]}"

    def test_the_value_is_reachable_only_deliberately(self, loaded):
        assert loaded.tns.api_key.get_secret_value() == "tns-bot-key"
        assert loaded.mast_credentials.password.get_secret_value() == "hunter2"

    def test_non_secret_fields_stay_readable(self, loaded):
        assert loaded.mast_credentials.user == "mast"


class TestNeverFatal:
    """Everything else in `config` raises ConfigError and the application stops. This
    must not: a telescope points perfectly well without a TNS key."""

    def test_a_missing_file_yields_an_empty_vault(self, tmp_path):
        v = load_vault(str(tmp_path / "absent.toml"))
        assert isinstance(v, VaultConfig)
        assert v.tns.api_key is None

    def test_malformed_toml_yields_an_empty_vault(self, tmp_path):
        p = tmp_path / "vault.toml"
        p.write_text("this is not = = valid toml [[[")
        assert load_vault(str(p)).tns.api_key is None

    def test_an_unexpected_shape_yields_an_empty_vault(self, tmp_path):
        p = tmp_path / "vault.toml"
        p.write_text('[tns]\napi_key = 12345\n\n[mast_credentials]\nuser = ["not", "a", "string"]\n')
        assert load_vault(str(p)).mast_credentials.user is None

    def test_a_directory_where_the_file_should_be_yields_an_empty_vault(self, tmp_path):
        """Stands in for an unreachable share, which surfaces as OSError."""
        d = tmp_path / "vault.toml"
        d.mkdir()
        assert load_vault(str(d)).tns.api_key is None


class TestPartialVaults:
    def test_a_section_may_be_absent(self, tmp_path):
        p = tmp_path / "vault.toml"
        p.write_text('[tns]\napi_key = "only-tns"\n')
        v = load_vault(str(p))
        assert v.tns.api_key.get_secret_value() == "only-tns"
        assert v.mast_credentials.user is None, "an absent section is a degraded capability, not an error"

    def test_an_empty_vault_is_valid(self, tmp_path):
        p = tmp_path / "vault.toml"
        p.write_text("")
        v = load_vault(str(p))
        assert v.tns == TnsVault() and v.mast_credentials == MastCredentials()
