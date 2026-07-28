import pytest

from nxre.secrets import (
    SecretStore,
    find_secret_refs,
    redact_secrets,
    resolve_secrets,
)

RULE = {
    "id": "abc12345",
    "action": {
        "type": "http",
        "url": "http://10.1.31.205/axis-cgi/activate.cgi",
        "auth": {"authType": "authBasicAndDigest", "login": "root", "password": "s3cr3t"},
    },
}


def test_redact_then_resolve_roundtrip():
    store = SecretStore()
    redacted = redact_secrets(RULE, store, name_prefix="TWG.abc12345")

    # password is replaced by a placeholder; the real value is stashed
    placeholder = redacted["action"]["auth"]["password"]
    assert placeholder.startswith("${secret:")
    assert "s3cr3t" not in str(redacted)
    assert "s3cr3t" in store.values.values()

    resolved = resolve_secrets(redacted, store)
    assert resolved["action"]["auth"]["password"] == "s3cr3t"
    # non-secret fields untouched
    assert resolved["action"]["auth"]["login"] == "root"


def test_find_refs_lists_names():
    store = SecretStore()
    redacted = redact_secrets(RULE, store, name_prefix="p")
    refs = find_secret_refs(redacted)
    assert len(refs) == 1
    assert refs[0] in store.values


def test_resolve_missing_secret_raises():
    with pytest.raises(KeyError):
        resolve_secrets({"password": "${secret:nope}"}, SecretStore())


def test_already_redacted_not_double_wrapped():
    store = SecretStore()
    data = {"password": "${secret:already.there}"}
    out = redact_secrets(data, store)
    assert out["password"] == "${secret:already.there}"
    assert store.values == {}


def test_store_save_load(tmp_path):
    path = tmp_path / "secrets.local.yaml"
    store = SecretStore(path=path)
    store.set("a.b", "hunter2")
    store.save()
    reloaded = SecretStore.load(path)
    assert reloaded.get("a.b") == "hunter2"
