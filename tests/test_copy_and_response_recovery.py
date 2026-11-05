"""No live credits: conservative copy checks and received-response accounting."""

import copy
import json
import pytest
import requests
from video_agent.connections import ProviderResponseError, api_json
from video_agent.control import ControlStore, DEFAULT_LIMITS, AllowanceExceeded
from video_agent.models import CampaignPlan, CopyReview
from video_agent.planning import Planner
from video_agent.validation import plan_copy_issues
from test_workflow import studio as studio, make_campaign


@pytest.mark.parametrize(
    "cta",
    [
        "Never charge again",
        "Wake up feeling younger",
        "Explore Trail Bottle and never charge again",
    ],
)
def test_cta_promises_require_approved_facts(studio, cta):
    with pytest.raises(ValueError, match="Fact review required"):
        make_campaign(studio, cta=cta)


def test_cta_can_reference_an_explicitly_approved_fact(studio):
    cid = make_campaign(studio, cta="Never charge again", claims=["Never charge again"])
    state = studio.snapshot(cid)["state"]
    assert all(ad["scenes"][-1]["claim_ids"] for ad in state["plan"]["ads"])
    assert not plan_copy_issues(
        CampaignPlan.model_validate(state["plan"]), state["brief"]
    )


def test_new_campaign_uses_server_defaults_and_edits_keep_saved_policy(
    studio, monkeypatch
):
    monkeypatch.setenv("CFC_PLANNING_MODEL", "gemini-server-model")
    monkeypatch.setenv("CFC_DEFAULT_LIMITS_JSON", json.dumps({"model_tokens": 90000}))
    monkeypatch.setenv(
        "CFC_DEFAULT_USAGE_RATES_JSON", json.dumps({"model_tokens": 2.5})
    )
    cid = make_campaign(studio)
    before = studio.snapshot(cid)
    brief = before["state"]["brief"]
    assert brief["planning_model"] == "gemini-server-model"
    assert brief["limits"]["model_tokens"] == 90000
    assert brief["usage_rates"] == {"model_tokens": 2.5}
    monkeypatch.setenv("CFC_PLANNING_MODEL", "gemini-another-model")
    monkeypatch.setenv("CFC_DEFAULT_LIMITS_JSON", "{}")
    monkeypatch.setenv("CFC_DEFAULT_USAGE_RATES_JSON", "{}")
    studio.update_brief(cid, {"audience": "Different audience"}, before["signature"])
    after = studio.snapshot(cid)["state"]["brief"]
    assert all(
        after[key] == brief[key] for key in ["planning_model", "limits", "usage_rates"]
    )


@pytest.mark.parametrize(
    "fact,headline",
    [
        ("Not dishwasher safe.", "Dishwasher safe"),
        ("Dishwasher safe only on the top rack.", "Dishwasher safe"),
        ("Water resistant except under pressure.", "Water resistant"),
        ("Up to 42 hours total playback.", "42 hours playback"),
        ("Suitable for indoor use only.", "Suitable for use"),
        ("8+ hours of playback.", "8 hours of playback"),
    ],
)
def test_headline_cannot_drop_negation_conditions_or_qualifiers(studio, fact, headline):
    cid = make_campaign(studio, claims=[fact])
    state = studio.snapshot(cid)["state"]
    plan = copy.deepcopy(state["plan"])
    plan["ads"][0]["scenes"][1]["headline"] = headline
    issues = plan_copy_issues(CampaignPlan.model_validate(plan), state["brief"])
    assert any(
        "Fact review required" in issue and "headline" in issue for issue in issues
    )


def planner_with_control(tmp_path):
    planner = Planner(
        tmp_path / "campaign", {"GEMINI_API_KEY": "fake"}, {}, lambda s: None
    )
    planner.control = ControlStore(tmp_path / "control.sqlite")
    planner.cid = "campaign"
    return planner


def envelope(text, tokens):
    return {
        "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": text}]}
        ],
        "usage": {"total_tokens": tokens},
    }


@pytest.mark.parametrize(
    "bad", ["", "{", '{"score":', '{"score":"invalid"}', '{"score":99}']
)
def test_received_invalid_response_settles_usage_and_recovers_after_restart(
    tmp_path, monkeypatch, bad
):
    planner = planner_with_control(tmp_path)
    prompt = json.dumps({"brief": {"limits": DEFAULT_LIMITS}})
    calls = []

    def first(payload):
        calls.append(payload)
        return envelope(bad, 17)

    monkeypatch.setattr(planner, "gemini_request", first)
    with pytest.raises(ValueError, match="response validation failed"):
        planner.model_json(prompt, CopyReview)
    task = planner.control.tasks(planner.cid)[0]
    assert task["status"] == "validation_failed" and task["attempts"] == 1
    usage = planner.control.usage(planner.cid)
    assert len(usage) == 1 and usage[0]["status"] == "settled"
    assert json.loads(usage[0]["actual"])["model_tokens"] == 17
    restored = planner_with_control(tmp_path)

    def second(payload):
        calls.append(payload)
        return envelope('{"score":9}', 11)

    monkeypatch.setattr(restored, "gemini_request", second)
    assert restored.model_json(prompt, CopyReview).score == 9
    assert restored.model_json(prompt, CopyReview).score == 9
    assert len(calls) == 2  # The final lookup uses the verified parsed-response cache.
    usage = restored.control.usage(restored.cid)
    assert all(row["status"] == "settled" for row in usage)
    assert sum(json.loads(row["actual"])["model_tokens"] for row in usage) == 28


def test_invalid_response_retry_respects_remaining_allowance(tmp_path, monkeypatch):
    planner = planner_with_control(tmp_path)
    prompt = json.dumps({"brief": {"limits": dict(DEFAULT_LIMITS, model_tokens=10000)}})
    calls = []
    monkeypatch.setattr(
        planner, "gemini_request", lambda p: calls.append(p) or envelope("{", 3000)
    )
    with pytest.raises(ValueError, match="validation failed"):
        planner.model_json(prompt, CopyReview)
    with pytest.raises(AllowanceExceeded):
        planner.model_json(prompt, CopyReview)
    assert len(calls) == 1
    assert all(row["status"] == "settled" for row in planner.control.usage(planner.cid))


def test_validation_retry_limit_and_unknown_network_outcome(tmp_path, monkeypatch):
    planner = planner_with_control(tmp_path)
    prompt = json.dumps({"brief": {"limits": DEFAULT_LIMITS}})
    calls = []
    monkeypatch.setattr(
        planner, "gemini_request", lambda p: calls.append(p) or envelope("{", 5)
    )
    for _ in range(2):
        with pytest.raises(ValueError):
            planner.model_json(prompt, CopyReview)
    with pytest.raises(RuntimeError, match="retry limit"):
        planner.model_json(prompt, CopyReview)
    assert len(calls) == 2
    unknown = planner_with_control(tmp_path / "other")

    def timeout(payload):
        raise requests.Timeout("No response arrived")

    monkeypatch.setattr(unknown, "gemini_request", timeout)
    with pytest.raises(requests.Timeout):
        unknown.model_json(prompt, CopyReview)
    with pytest.raises(RuntimeError, match="reconcile"):
        unknown.model_json(prompt, CopyReview)
    assert unknown.control.tasks(unknown.cid)[0]["status"] == "unknown"
    assert unknown.control.usage(unknown.cid)[0]["status"] == "reserved"


def test_non_json_http_response_is_known_not_unknown(tmp_path, monkeypatch):
    planner = planner_with_control(tmp_path)

    class Response:
        ok, status_code = True, 200

        def json(self):
            raise ValueError("truncated HTTP JSON")

    monkeypatch.setattr(
        "video_agent.connections.requests.request", lambda *a, **k: Response()
    )
    with pytest.raises(ProviderResponseError):
        api_json("Gemini", "POST", "https://example.test")
    with pytest.raises(ValueError, match="failed validation"):
        planner.model_json(json.dumps({"brief": {}}), CopyReview)
    assert planner.control.tasks(planner.cid)[0]["status"] == "validation_failed"
    assert planner.control.usage(planner.cid)[0]["status"] == "settled"
