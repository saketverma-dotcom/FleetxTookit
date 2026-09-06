"""FleetX login memory (v3.13.2).

Regression: the email + Bearer token were not being remembered. Two causes —
  1. the pre-fill read only load_credentials(), which is populated ONLY by an
     email+password login with "Remember me" ticked, so a manual-token login
     left the email blank and the token lookup (keyed by email) always missed;
  2. the token save sat inside the `if remember:` branch, so unticking the box
     discarded the token too.
The email + token are now always remembered; the checkbox governs the password.
"""
import json
import os

import pytest


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import keyring
    import keyring.backend

    class Mem(keyring.backend.KeyringBackend):
        priority = 1

        def __init__(self):
            self.d = {}

        def set_password(self, s, u, p):
            self.d[(s, u)] = p

        def get_password(self, s, u):
            return self.d.get((s, u))

        def delete_password(self, s, u):
            self.d.pop((s, u), None)

    keyring.set_keyring(Mem())
    from fleetx_toolkit import config as cf
    from fleetx_toolkit import storage as st
    monkeypatch.setattr(cf, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(st, "CRED_FILE", str(tmp_path / "cred.json"))
    return cf, st


class TestBearerTokenStorage:
    def test_save_and_load(self, isolated):
        _, st = isolated
        assert st.save_bearer_token("a@fleetx.io", "TOK1")
        assert st.load_bearer_token("a@fleetx.io") == "TOK1"

    def test_per_email(self, isolated):
        _, st = isolated
        st.save_bearer_token("a@fleetx.io", "TOK_A")
        st.save_bearer_token("b@fleetx.io", "TOK_B")
        assert st.load_bearer_token("a@fleetx.io") == "TOK_A"
        assert st.load_bearer_token("b@fleetx.io") == "TOK_B"

    def test_missing_returns_empty(self, isolated):
        _, st = isolated
        assert st.load_bearer_token("nobody@fleetx.io") == ""
        assert st.load_bearer_token("") == ""

    def test_clear(self, isolated):
        _, st = isolated
        st.save_bearer_token("a@fleetx.io", "TOK")
        st.clear_bearer_token("a@fleetx.io")
        assert st.load_bearer_token("a@fleetx.io") == ""


class TestLastEmailFallback:
    def test_settings_records_last_email(self, isolated):
        cf, _ = isolated
        s = cf.load_settings()
        s["last_email"] = "dinesh.d@fleetx.io"
        cf.save_settings(s)
        assert cf.load_settings()["last_email"] == "dinesh.d@fleetx.io"
        assert json.load(open(cf.SETTINGS_FILE))["last_email"] == "dinesh.d@fleetx.io"

    def test_token_recoverable_without_saved_credentials(self, isolated):
        """The manual-token case: no saved password, but email+token persist."""
        cf, st = isolated
        email = "dinesh.d@fleetx.io"
        s = cf.load_settings(); s["last_email"] = email; cf.save_settings(s)
        st.save_bearer_token(email, "MYBEARER999")
        # load_credentials has nothing (no password login happened)
        assert st.load_credentials() == ("", "")
        # but the fallback path finds both
        recovered = cf.load_settings().get("last_email", "")
        assert recovered == email
        assert st.load_bearer_token(recovered) == "MYBEARER999"
