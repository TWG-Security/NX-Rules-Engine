import json
import stat
import time

from nxre.client.auth import Token
from nxre.session import SessionStore


def _store(tmp_path):
    return SessionStore(tmp_path / "session.json")


def test_save_load_roundtrip(tmp_path):
    store = _store(tmp_path)
    token = Token(value="tok-abc", expires_at=time.time() + 600)
    store.save("TWG", token, "msupczenski")

    loaded = store.load("TWG")
    assert loaded is not None
    assert loaded.value == "tok-abc"
    assert loaded.is_valid()
    assert store.username("TWG") == "msupczenski"


def test_password_never_persisted(tmp_path):
    store = _store(tmp_path)
    store.save("TWG", Token(value="tok", expires_at=time.time() + 600), "user")
    raw = (tmp_path / "session.json").read_text(encoding="utf-8")
    # Only token/expiry/username are stored — nothing password-shaped.
    assert "password" not in raw.lower()
    assert set(json.loads(raw)["TWG"]) == {"username", "token", "expires_at"}


def test_file_is_owner_only(tmp_path):
    store = _store(tmp_path)
    store.save("TWG", Token(value="tok", expires_at=time.time() + 600), "user")
    mode = stat.S_IMODE((tmp_path / "session.json").stat().st_mode)
    assert mode == 0o600


def test_expired_token_loads_but_is_invalid(tmp_path):
    store = _store(tmp_path)
    store.save("TWG", Token(value="old", expires_at=time.time() - 10), "user")
    loaded = store.load("TWG")
    assert loaded is not None and not loaded.is_valid()


def test_missing_and_unknown_system(tmp_path):
    store = _store(tmp_path)
    assert store.load("TWG") is None  # no file yet
    store.save("TWG", Token(value="t", expires_at=time.time() + 600), "user")
    assert store.load("Other") is None


def test_clear_one_and_all(tmp_path):
    store = _store(tmp_path)
    store.save("TWG", Token(value="a", expires_at=time.time() + 600), "u1")
    store.save("Bethel", Token(value="b", expires_at=time.time() + 600), "u2")

    assert store.clear("TWG") is True
    assert store.load("TWG") is None
    assert store.load("Bethel") is not None  # untouched

    assert store.clear("nope") is False
    assert store.clear(None) is True  # drops the remaining file
    assert store.load("Bethel") is None
    assert store.clear(None) is False  # nothing left


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("{ not json", encoding="utf-8")
    store = SessionStore(path)
    assert store.load("TWG") is None
    # and a subsequent save recovers cleanly
    store.save("TWG", Token(value="t", expires_at=time.time() + 600), "u")
    assert store.load("TWG").value == "t"
