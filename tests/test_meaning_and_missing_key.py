"""No provider credits: currency/unit meaning and missing-key recovery."""

import copy
import json

import pytest

from test_workflow import studio as studio, make_campaign, perform, stub_runtime
from video_agent.facts import normalized
from video_agent.models import CampaignPlan, CopyReview
from video_agent.planning import Planner
from video_agent.control import ControlStore
from video_agent.validation import plan_copy_issues


@pytest.mark.parametrize("field", ["headline", "narration", "caption", "footnote"])
@pytest.mark.parametrize(
    "fact,changed",
    [
        ("Price ₹500", "Price $500"),
        ("Price €500", "Price £500"),
        ("Price ₩500", "Price 500"),
        ("Capacity 500 mL", "Capacity 500 ML"),
        ("Power 5 mW", "Power 5 MW"),
        ("Transfer 5 MB/s", "Transfer 5 Mb/s"),
        ("Speed 5 m/s", "Speed 5 ms"),
        ("Area 5 m²", "Area 5 m³"),
        ("Temperature 50 °C", "Temperature 50 °F"),
        ("Concentration 5 mg/ml", "Concentration 5 g/ml"),
    ],
)
def test_currency_and_units_cannot_change_in_any_copy_field(
    studio, field, fact, changed
):
    cid = make_campaign(studio, claims=[fact])
    state = studio.snapshot(cid)["state"]
    plan = copy.deepcopy(state["plan"])
    scene = plan["ads"][0]["scenes"][1]
    # Establish the approved statement itself is valid in every field.
    scene[field] = fact
    assert not plan_copy_issues(CampaignPlan.model_validate(plan), state["brief"])
    scene[field] = changed
    issues = plan_copy_issues(CampaignPlan.model_validate(plan), state["brief"])
    assert normalized(fact) != normalized(changed)
    assert any("Fact review required" in issue and field in issue for issue in issues)


@pytest.mark.parametrize(
    "left,right",
    [
        ("8 mm drivers", "eight millimetres drivers"),
        ("Price ₹500", "Price INR 500"),
        ("Capacity 500 ml", "Capacity 500 milliliters"),
        ("Temperature 50 ℃", "Temperature 50 °C"),
    ],
)
def test_explicit_equivalent_spellings_are_not_unit_conversions(left, right):
    assert normalized(left) == normalized(right)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_key_precedes_model_access_request_and_reservation(
    tmp_path, monkeypatch, key
):
    p = Planner(tmp_path, dict(GEMINI_API_KEY=key), {}, lambda s: None)
    p.control = ControlStore(tmp_path / "control.sqlite")
    monkeypatch.setattr(
        "video_agent.planning.api_json",
        lambda *a, **k: pytest.fail("Missing key must not call any model endpoint"),
    )
    with pytest.raises(ValueError, match="GEMINI_API_KEY is missing"):
        p.check_model_access("gemini-2.5-flash")
    with pytest.raises(ValueError, match="saved draft"):
        p.model_json(json.dumps({"brief": {}}), CopyReview)
    assert not p.control.usage(p.cid) and not p.control.tasks(p.cid)


def test_missing_key_ai_revision_keeps_saved_plan_and_manual_fallback(
    studio, monkeypatch
):
    cid = make_campaign(studio)
    runtime, _rendered = stub_runtime(studio, cid)
    perform(studio, cid, "approve")
    perform(studio, cid, "generate")
    before = studio.snapshot(cid)
    runtime.planner.revise_ad = Planner.revise_ad.__get__(runtime.planner)
    monkeypatch.setitem(studio.secrets, "GEMINI_API_KEY", "")
    runtime.planner.keys["GEMINI_API_KEY"] = ""
    monkeypatch.setattr(
        "video_agent.planning.api_json",
        lambda *a, **k: pytest.fail("Revision without a key must not access a model"),
    )
    studio.act(cid, "revise", before["signature"], feedback="Improve the opening")
    studio.jobs.wait(cid)
    failed = studio.snapshot(cid)
    assert (
        failed["job"]["status"] == "failed"
        and "GEMINI_API_KEY is missing" in failed["job"]["error"]
    )
    assert failed["state"]["plan"] == before["state"]["plan"]
    studio.use_saved_draft(cid, failed["signature"])
    recovered = studio.snapshot(cid)
    assert recovered["stage"] == "approve_plan" and not recovered["approved"]
    manual = copy.deepcopy(recovered["state"]["plan"])
    manual["summary"] = "Manually reviewed campaign direction"
    studio.save_manual_plan(cid, manual, recovered["signature"])
    assert studio.snapshot(cid)["state"]["plan"]["summary"] == manual["summary"]
