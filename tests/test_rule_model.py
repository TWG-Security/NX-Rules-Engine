from nxre.models.rule import NativeRule

API_RULE = {
    "id": "r1",
    "etag": "abc",
    "comment": "hi",
    "enabled": True,
    "event": {"type": "motion", "devices": {"acceptAll": True}},
    "action": {"type": "writeToLog", "intervalS": 60},
    "schedule": [],
}


def test_from_api_and_types():
    r = NativeRule.from_api(API_RULE)
    assert r.event_type == "motion"
    assert r.action_type == "writeToLog"
    assert r.id == "r1"


def test_api_body_excludes_identity():
    r = NativeRule.from_api(API_RULE)
    body = r.to_api_body()
    assert "id" not in body and "etag" not in body
    assert set(body) == {"event", "action", "enabled", "schedule", "comment"}


def test_fingerprint_ignores_id_and_etag():
    a = NativeRule.from_api(API_RULE)
    b = NativeRule.from_api({**API_RULE, "id": "different", "etag": "zzz"})
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_changes_with_content():
    a = NativeRule.from_api(API_RULE)
    b = NativeRule.from_api({**API_RULE, "enabled": False})
    assert a.fingerprint() != b.fingerprint()


def test_yaml_obj_order_and_etag_last():
    r = NativeRule.from_api(API_RULE)
    keys = list(r.to_yaml_obj().keys())
    assert keys[0] == "id"
    assert keys[-1] == "etag"
