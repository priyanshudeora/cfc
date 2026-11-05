import copy
import threading
import pytest
from video_agent.control import ControlStore, AllowanceExceeded, DEFAULT_LIMITS
from video_agent.storage import signature
from video_agent.timeline import ad_key
from video_agent.facts import normalized
from video_agent.models import CampaignPlan
from video_agent.validation import plan_copy_issues
from test_workflow import studio as studio, make_campaign, perform, stub_runtime


def test_review_ids_are_single_use_and_bound_to_stage_campaign_and_media(studio):
    cid = make_campaign(studio)
    _, rendered = stub_runtime(studio, cid)
    plan = studio.snapshot(cid)
    ready = perform(studio, cid, "approve")
    assert plan["signature"] != ready["signature"]
    with pytest.raises(ValueError):
        studio.act(cid, "generate", plan["signature"])
    sample = perform(studio, cid, "generate")
    assert sample["signature"] not in [plan["signature"], ready["signature"]]
    for stale in [plan, ready]:
        with pytest.raises(ValueError):
            studio.act(cid, "approve", stale["signature"])
    other = make_campaign(studio)
    with pytest.raises(ValueError):
        studio.act(other, "approve", sample["signature"])
    from pathlib import Path

    Path(sample["state"]["sample"]["video"]).write_bytes(b"changed after review")
    with pytest.raises(ValueError):
        studio.act(cid, "approve", sample["signature"])
    assert rendered == ["product_demo"]
    with pytest.raises(ValueError):
        studio.repository.control.require_grant(
            cid, "batch", signature(sample["state"])
        )


def test_atomic_review_consumption_across_connections(tmp_path):
    path = tmp_path / "control.sqlite"
    first, second = ControlStore(path), ControlStore(path)
    token = first.issue(
        "one",
        "approve_plan",
        "checkpoint",
        {"plan": "a"},
        ["approve"],
        {"plan": "a", "ads": []},
    )
    results = []

    def consume(store):
        try:
            store.consume(
                token,
                "one",
                "approve_plan",
                "checkpoint",
                {"plan": "a"},
                "approve",
                "receipt",
            )
            results.append(True)
        except ValueError:
            results.append(False)

    workers = [
        threading.Thread(target=consume, args=(store,)) for store in [first, second]
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert sorted(results) == [False, True]
    with pytest.raises(ValueError):
        first.receipt(token, "one", "approve_sample", "approve", "receipt")
    with pytest.raises(ValueError):
        first.receipt(
            token, "one", "approve_plan", "approve", "receipt", checkpoint="different"
        )


@pytest.mark.parametrize("field", ["headline", "narration", "caption", "footnote"])
def test_manual_unsupported_facts_require_explicit_review(studio, field):
    cid = make_campaign(studio)
    snap = studio.snapshot(cid)
    plan = copy.deepcopy(snap["state"]["plan"])
    scene = plan["ads"][0]["scenes"][0]
    scene[field] = "Lifetime warranty included."
    if field in ["narration", "caption"]:
        scene["caption"] = scene["narration"] = "Lifetime warranty included."
    with pytest.raises(ValueError, match="Fact review required"):
        studio.save_manual_plan(cid, plan, snap["signature"])
    assert studio.snapshot(cid)["state"]["plan"] == snap["state"]["plan"]
    studio.approve_pending_facts(cid, snap["signature"])
    after = studio.snapshot(cid)
    assert not after["approved"] and after["stage"] == "approve_plan"
    assert (
        "Lifetime warranty included."
        in after["state"]["brief"]["approved_claims"].values()
    )


def test_spelled_numbers_and_spoken_qualifiers(studio):
    fact = "Up to 42 hours total playback, including the charging case."
    cid = make_campaign(studio, claims=[fact])
    state = studio.snapshot(cid)["state"]
    plan = copy.deepcopy(state["plan"])
    scenes = plan["ads"][0]["scenes"]
    for scene, seconds in zip(scenes, [3, 7, 3, 3, 4]):
        scene["seconds"] = seconds
    scenes[1]["narration"] = scenes[1]["caption"] = (
        "Up to forty-two hours total playback, including the charging case."
    )
    assert normalized(fact) == normalized(scenes[1]["narration"])
    assert not plan_copy_issues(CampaignPlan.model_validate(plan), state["brief"])
    scenes[1]["narration"] = scenes[1]["caption"] = "Forty-two hours playback."
    issues = plan_copy_issues(CampaignPlan.model_validate(plan), state["brief"])
    assert any("spoken qualifiers" in issue for issue in issues)
    assert normalized("one hundred and five") == "105"


def test_usage_reservations_exhaustion_and_persistence(tmp_path):
    store = ControlStore(tmp_path / "control.sqlite")
    limits = dict(DEFAULT_LIMITS, speech_characters=10)
    store.reserve("one", "request1", {"speech_characters": 8}, limits)
    with pytest.raises(AllowanceExceeded):
        store.reserve("one", "request2", {"speech_characters": 3}, limits)
    restored = ControlStore(tmp_path / "control.sqlite")
    with pytest.raises(AllowanceExceeded):
        restored.reserve("one", "request1", {"speech_characters": 8}, limits)
    restored.settle("one", "request1", {"speech_characters": 5})
    assert restored.reserve("one", "request2", {"speech_characters": 3}, limits)


def test_ai_ad_revision_routes_full_campaign_to_fact_review(studio, monkeypatch):
    from video_agent.models import AdPlan

    cid = make_campaign(studio)
    snap = studio.snapshot(cid)
    planner = studio.runtime(cid).planner
    revised = copy.deepcopy(snap["state"]["plan"]["ads"][2])
    revised["scenes"][0]["headline"] = "Lifetime warranty included."
    monkeypatch.setattr(
        planner, "model_json", lambda *args: AdPlan.model_validate(revised)
    )
    with pytest.raises(ValueError, match="Fact review required"):
        planner.revise_ad(snap["state"], 2, "Add a warranty statement")
    studio.approve_pending_facts(cid, snap["signature"])
    after = studio.snapshot(cid)
    assert len(after["state"]["plan"]["ads"]) == 3
    assert after["state"]["plan"]["ads"][:2] == snap["state"]["plan"]["ads"][:2]
    assert after["stage"] == "approve_plan" and not after["approved"]


def test_plan_version_is_separate_from_media_identity(studio):
    cid = make_campaign(studio)
    state = studio.snapshot(cid)["state"]
    key = ad_key(state["plan"]["ads"][0], state["brief"])
    changed = dict(state, version=state["version"] + 1)
    assert signature(changed) != signature(state)
    assert ad_key(changed["plan"]["ads"][0], changed["brief"]) == key


def test_cta_cannot_hide_an_unapproved_factual_claim(studio):
    with pytest.raises(ValueError, match="Fact review required"):
        make_campaign(studio, cta="Lifetime warranty included.")
    cid = make_campaign(studio, product="Battery Pack")
    assert studio.snapshot(cid)["stage"] == "approve_plan"


def test_note_only_visual_change_is_rejected(studio):
    cid = make_campaign(studio)
    current = studio.snapshot(cid)
    plan = copy.deepcopy(current["state"]["plan"])
    plan["ads"][0]["scenes"][0]["visual_direction"] = (
        "Make a new camera orbit around the product."
    )
    with pytest.raises(ValueError, match="structured timeline"):
        studio.save_manual_plan(cid, plan, current["signature"])


@pytest.mark.parametrize("change", ["file", "version"])
def test_final_review_rejects_earlier_stage_ids_and_changed_outputs(studio, change):
    from pathlib import Path

    cid = make_campaign(studio)
    stub_runtime(studio, cid)
    plan = studio.snapshot(cid)
    perform(studio, cid, "approve")
    sample = perform(studio, cid, "generate")
    final = perform(studio, cid, "approve")
    assert final["stage"] == "approve_final"
    for stale in [plan, sample]:
        with pytest.raises(ValueError):
            studio.act(cid, "approve", stale["signature"])
    scope = studio.repository.control.require_grant(
        cid, "batch", signature(final["state"])
    )
    assert (
        ad_key(final["state"]["plan"]["ads"][0], final["state"]["brief"])
        not in scope["ads"]
    )
    if change == "file":
        Path(final["state"]["outputs"][2]["captions"]).write_text("changed captions")
    else:
        state = final["state"]
        state["outputs"][2]["version"] += 1
        studio.runtime(cid).agent.update_state(
            studio.runtime(cid).config, {"outputs": state["outputs"]}
        )
    with pytest.raises(ValueError):
        studio.act(cid, "approve", final["signature"])
    assert not studio.snapshot(cid)["state"].get("export_path")
