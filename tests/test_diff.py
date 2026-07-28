from nxre.models.rule import NativeRule
from nxre.rules.diff import ChangeType, WriteClass, build_plan

WEBHOOK = "http://127.0.0.1:8787"


def rule(rid, *, enabled=True, comment="c", event=None, action=None):
    return NativeRule.from_api({
        "id": rid,
        "enabled": enabled,
        "comment": comment,
        "event": event or {"type": "motion"},
        "action": action or {"type": "writeToLog"},
        "schedule": [],
    })


def entry_for(plan, rid):
    return next(e for e in plan.entries if e.rule_id == rid)


def test_noop_when_identical():
    r = rule("r1")
    plan = build_plan([r], [rule("r1")])
    assert plan.is_empty()


def test_safe_update_enabled_only():
    plan = build_plan([rule("r1", enabled=False)], [rule("r1", enabled=True)])
    e = entry_for(plan, "r1")
    assert e.change is ChangeType.UPDATE
    assert e.write_class is WriteClass.SAFE
    assert e.changed_fields == ["enabled"]


def test_guarded_update_event_change():
    plan = build_plan(
        [rule("r1", event={"type": "cameraInput"})],
        [rule("r1", event={"type": "motion"})],
    )
    e = entry_for(plan, "r1")
    assert e.change is ChangeType.UPDATE
    assert e.write_class is WriteClass.GUARDED


def test_safe_create_write_to_log():
    plan = build_plan([rule("new1", action={"type": "writeToLog"})], [])
    e = entry_for(plan, "new1")
    assert e.change is ChangeType.CREATE
    assert e.write_class is WriteClass.SAFE


def test_guarded_create_http():
    plan = build_plan(
        [rule("new2", action={"type": "http", "url": "http://example/x"})],
        [], webhook_url=WEBHOOK,
    )
    assert entry_for(plan, "new2").write_class is WriteClass.GUARDED


def test_safe_create_webhook_rule():
    action = {"type": "http", "url": f"{WEBHOOK}/webhook/nx"}
    plan = build_plan([rule("hook", action=action)], [], webhook_url=WEBHOOK)
    assert entry_for(plan, "hook").write_class is WriteClass.SAFE


def test_blocked_when_not_writable():
    plan = build_plan([rule("r1", enabled=False)], [rule("r1")], system_writable=False)
    assert entry_for(plan, "r1").write_class is WriteClass.BLOCKED


def test_prune_creates_delete_entries():
    plan = build_plan([], [rule("stale")], prune=True)
    e = entry_for(plan, "stale")
    assert e.change is ChangeType.DELETE
    assert e.write_class is WriteClass.GUARDED


def test_no_prune_skips_deletes():
    plan = build_plan([], [rule("stale")], prune=False)
    assert plan.is_empty()
