from datetime import time

import httpx
import respx

from nxre.engine.actions.builtin import register_builtin_actions
from nxre.engine.actions.registry import ActionRegistry
from nxre.engine.automations import AutomationEngine
from nxre.engine.bus import Event, EventBus
from nxre.engine.conditions import evaluate_all
from nxre.models.automation import Automation, Condition


def _event(**kw):
    base = {"type": "motion", "source": "Lobby Cam", "caption": "", "description": ""}
    base.update(kw)
    return Event(**base)


# -- conditions -------------------------------------------------------------
def test_condition_source_and_caption_contains():
    ev = _event(source="Lobby Cam", caption="Person detected")
    assert evaluate_all([Condition(condition="source_contains", value="lobby")], ev)
    assert not evaluate_all([Condition(condition="source_contains", value="garage")], ev)
    assert evaluate_all([Condition(condition="caption_contains", value="person")], ev)


def test_condition_event_type_is():
    ev = _event(type="deviceDisconnected")
    assert evaluate_all([Condition(condition="event_type_is", value="deviceDisconnected")], ev)
    assert not evaluate_all([Condition(condition="event_type_is", value="motion")], ev)


def test_condition_time_between_normal_and_overnight():
    ev = _event()
    # 09:00 window 08:00-17:00 → inside
    assert evaluate_all([Condition(condition="time_between", after="08:00", before="17:00")],
                        ev, now=time(9, 0))
    assert not evaluate_all([Condition(condition="time_between", after="08:00", before="17:00")],
                            ev, now=time(19, 0))
    # overnight window 22:00-06:00
    assert evaluate_all([Condition(condition="time_between", after="22:00", before="06:00")],
                        ev, now=time(23, 30))
    assert not evaluate_all([Condition(condition="time_between", after="22:00", before="06:00")],
                            ev, now=time(12, 0))


def test_unknown_condition_passes():
    assert evaluate_all([Condition(condition="mystery", value="x")], _event())


# -- end-to-end engine ------------------------------------------------------
async def test_engine_runs_action_when_trigger_and_condition_pass():
    calls = []
    reg = ActionRegistry()
    reg.register("spy", lambda cfg, ev, ctx: calls.append((cfg, ev)) or _noop())

    auto = Automation.from_yaml_obj({
        "alias": "lobby motion",
        "trigger": [{"platform": "nx_event", "event_type": "motion", "source": "Lobby"}],
        "condition": [{"condition": "caption_contains", "value": "person"}],
        "action": [{"kind": "spy", "note": "hi"}],
    })
    bus = EventBus()
    AutomationEngine([auto], registry=reg).attach(bus)

    await bus.publish(_event(source="Lobby Cam", caption="Person detected"))
    assert len(calls) == 1                    # trigger + condition matched
    await bus.publish(_event(source="Lobby Cam", caption="nothing"))
    assert len(calls) == 1                    # condition blocked the second one


async def _noop():
    return None


@respx.mock
async def test_http_action_fires_with_substitution():
    route = respx.post("https://hook.example/notify").mock(return_value=httpx.Response(200))
    reg = ActionRegistry()
    register_builtin_actions(reg)
    auto = Automation.from_yaml_obj({
        "alias": "notify",
        "trigger": [{"platform": "nx_event", "event_type": "motion"}],
        "action": [{"kind": "http", "method": "POST",
                    "url": "https://hook.example/notify",
                    "body": '{"what":"{type}","where":"{source}"}'}],
    })
    bus = EventBus()
    AutomationEngine([auto], registry=reg).attach(bus)

    await bus.publish(_event(type="motion", source="Lobby Cam"))
    assert route.called
    assert b'"what":"motion"' in route.calls.last.request.content
    assert b'"where":"Lobby Cam"' in route.calls.last.request.content


def test_disabled_automation_is_not_loaded():
    auto = Automation.from_yaml_obj({
        "alias": "off", "enabled": False,
        "trigger": [{"platform": "nx_event", "event_type": "motion"}],
        "action": [{"kind": "log"}],
    })
    engine = AutomationEngine([auto])
    assert engine.automations == []
