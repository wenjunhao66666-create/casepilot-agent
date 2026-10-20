import json
from pathlib import Path

import pytest

from examples import replay
from core.skill_loader import SkillManager


def reply(**overrides):
    return dict(request_id="r1", conv_id="c1", response="synthetic answer", primary_agent="technical",
                agent_types=["technical", "billing"], tools_used=[], **overrides)


def test_suite_has_unique_scenarios_and_compound_coverage():
    cases = replay.load_cases(Path(replay.__file__).with_name("service_cases.json"))
    assert len(cases) == 7
    assert {"technical", "billing"} == set(next(c for c in cases if c["id"] == "compound")["required_agents"])


def test_rag_requires_success_flag_in_addition_to_tool_name():
    case = {"required_tools": ["search_knowledge_base"], "requires_knowledge": True}
    body = reply()
    body["tools_used"] = ["search_knowledge_base"]
    assert "knowledge_not_used" in replay.assess(case, body)
    body["knowledge_used"] = True
    assert replay.assess(case, body) == []


def test_partial_compound_routing_fails():
    body = reply()
    body["agent_types"] = ["technical"]
    assert "missing_required_agent" in replay.assess({"required_agents": ["technical", "billing"]}, body)


def test_transport_failure_does_not_count_as_latency_success(monkeypatch):
    def fail(*args):
        raise RuntimeError("http_503")
    monkeypatch.setattr(replay, "post_chat", fail)
    rows, summary = replay.run_cases([{"id": "a", "message": "hi"}], "http://localhost")
    assert rows[0]["failures"] == ["http_503"]
    assert summary["http_successes"] == 0 and summary["p95_wall_ms"] is None
    assert summary["contract_pass_rate"] == 0


def test_receipt_mismatch_fails_and_report_excludes_reply_and_token(monkeypatch):
    responses = [reply(), reply()]
    responses[1]["response"] = "different answer"
    monkeypatch.setattr(replay, "post_chat", lambda *args: responses.pop(0))
    rows, summary = replay.run_cases([{"id": "a", "message": "hi"}], "http://localhost", "test-secret", check_idempotency=True)
    assert rows[0]["failures"] == ["receipt_mismatch"]
    report = json.dumps([rows, summary])
    assert "test-secret" not in report and "synthetic answer" not in report


def test_invalid_shape_is_reported_without_crashing():
    assert replay.assess({}, []) == ["response_not_object"]
    body = reply()
    body["tools_used"] = None
    assert "invalid_tools_used" in replay.assess({}, body)


def test_duplicate_case_ids_are_rejected_before_requests(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"id": "same", "message": "x"}] * 2))
    with pytest.raises(ValueError):
        replay.load_cases(path)


def test_small_sample_percentiles_are_explicit():
    assert replay.percentile([], .95) is None
    assert replay.percentile([100], .95) == 100
    assert replay.percentile([0, 100], .95) == 95


def test_subscription_skill_is_loaded_and_scoped_to_service_roles():
    manager = SkillManager(str(Path(__file__).resolve().parents[1] / 'skills'))
    manager.load()
    assert not manager.errors
    prompt = manager.prompt_for('订阅登录401，同时重复扣款', 'technical')
    assert 'SaaS订阅问题分诊' in prompt
    assert '不把401当作重复扣款的原因' in prompt
    assert 'SaaS订阅问题分诊' not in manager.prompt_for('订阅问题', 'escalation')
