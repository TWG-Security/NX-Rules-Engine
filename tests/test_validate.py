from nxre.client.manifest import Manifest
from nxre.models.rule import NativeRule
from nxre.rules.validate import has_errors, validate_rule

MANIFEST = Manifest(
    events={"motion": {"fields": [{"fieldName": "deviceId", "properties": {"optional": False}}]}},
    actions={"writeToLog": {"fields": []}},
)


def make(event, action):
    return NativeRule.from_api({"event": event, "action": action})


def test_valid_rule_no_errors():
    issues = validate_rule(make({"type": "motion", "deviceId": "x"}, {"type": "writeToLog"}), MANIFEST)
    assert not has_errors(issues)


def test_unknown_event_type_is_error():
    issues = validate_rule(make({"type": "nope"}, {"type": "writeToLog"}), MANIFEST)
    assert has_errors(issues)


def test_unknown_action_type_is_error():
    issues = validate_rule(make({"type": "motion", "deviceId": "x"}, {"type": "nope"}), MANIFEST)
    assert has_errors(issues)


def test_missing_required_field_is_warning_not_error():
    issues = validate_rule(make({"type": "motion"}, {"type": "writeToLog"}), MANIFEST)
    assert not has_errors(issues)
    assert any(i.level == "warning" for i in issues)


def test_empty_manifest_skips_type_checks():
    issues = validate_rule(make({"type": "anything"}, {"type": "anything"}), Manifest({}, {}))
    assert not has_errors(issues)
