"""Stage policy/UI and source provenance checks; no inference is simulated as real."""

import copy
import pytest
from test_generation import configured as configured
from test_workflow import studio as studio, make_campaign, stub_runtime, perform
from test_app import app as app
from video_agent.generation_contract import stage_generation_issue
from video_agent.campaigns import template_plan
from video_agent.models import CampaignPlan
from video_agent.validation import plan_copy_issues
from video_agent.storage import save_json
from video_agent.cache import file_hash


def sample_only(api, monkeypatch, configured):
    scope = dict(configured["brief"]["generation"], batch_enabled=False)
    monkeypatch.setattr(api, "generation_scope", lambda: scope)
    cid = make_campaign(api, creation_mode="combine", limits={"generation_jobs": 15})
    stub_runtime(api, cid)
    return cid


@pytest.mark.parametrize("remaining", [1, 2])
def test_one_scene_sample_proceeds_but_any_ai_batch_is_blocked(
    studio, monkeypatch, configured, remaining
):
    cid = sample_only(studio, monkeypatch, configured)
    snap = studio.snapshot(cid)
    if remaining == 1:
        plan = copy.deepcopy(snap["state"]["plan"])
        plan["ads"][2]["scenes"][0]["edit"].update(
            mode="reused",
            generation_prompt="",
            conditioning_asset="",
            generation_settings=None,
        )
        studio.save_manual_plan(cid, plan, snap["signature"])
    assert not studio.snapshot(cid)["generation_blocker"]
    ready = perform(studio, cid, "approve")
    assert not ready["generation_blocker"]
    sample = perform(studio, cid, "generate")
    assert sample["stage"] == "approve_sample"
    assert "batches are disabled" in sample["generation_blocker"]
    before = studio.repository.control.tasks(cid)
    with pytest.raises(ValueError, match="batches are disabled"):
        studio.act(cid, "approve", sample["signature"])
    assert studio.repository.control.tasks(cid) == before
    assert studio.snapshot(cid)["stage"] == "approve_sample"


def test_two_generated_sample_scenes_are_blocked_before_approval(
    studio, monkeypatch, configured
):
    cid = sample_only(studio, monkeypatch, configured)
    snap = studio.snapshot(cid)
    plan = copy.deepcopy(snap["state"]["plan"])
    extra = plan["ads"][0]["scenes"][1]
    extra["asset_ids"] = ["product_main"]
    extra["edit"] = copy.deepcopy(plan["ads"][0]["scenes"][0]["edit"])
    extra["edit"]["seed"] += 10
    studio.save_manual_plan(cid, plan, snap["signature"])
    snap = studio.snapshot(cid)
    assert "one sample scene" in snap["generation_blocker"]
    with pytest.raises(ValueError, match="one sample scene"):
        studio.act(cid, "approve", snap["signature"])
    assert not studio.repository.control.usage(cid)


def test_missing_profile_and_zero_jobs_have_explicit_setup_steps(configured):
    brief = copy.deepcopy(configured["brief"])
    ads = template_plan(brief)["ads"][:1]
    brief.pop("generation")
    assert "COMFY_PROFILE" in stage_generation_issue(brief, ads, "sample")
    brief["generation"] = configured["brief"]["generation"]
    brief["limits"] = {"generation_jobs": 0}
    assert "CFC_DEFAULT_LIMITS_JSON" in stage_generation_issue(brief, ads, "sample")
    assert not stage_generation_issue(brief, ads, "sample", new_jobs=0)


def test_generate_mode_cannot_save_an_image_animation_scene(configured):
    brief = dict(configured["brief"], creation_mode="generate")
    plan = template_plan(brief)
    assert all(
        s["edit"]["mode"] == "generated" for a in plan["ads"] for s in a["scenes"]
    )
    plan["ads"][0]["scenes"][2]["edit"]["mode"] = "reused"
    assert any(
        "every scene" in s
        for s in plan_copy_issues(CampaignPlan.model_validate(plan), brief)
    )


def test_spent_allowance_blocks_further_work_but_retains_sample(
    studio, monkeypatch, configured
):
    scope = configured["brief"]["generation"]
    monkeypatch.setattr(studio, "generation_scope", lambda: scope)
    cid = make_campaign(studio, creation_mode="combine", limits={"generation_jobs": 1})
    stub_runtime(studio, cid)
    perform(studio, cid, "approve")
    sample = perform(studio, cid, "generate")
    studio.repository.control.reserve(
        cid, "used-job", {"generation_jobs": 1}, sample["state"]["brief"]["limits"]
    )
    studio.repository.control.settle(cid, "used-job", {"generation_jobs": 1})
    snap = studio.snapshot(cid)
    assert "exhausted" in snap["generation_blocker"]
    assert snap["state"]["sample"] == sample["state"]["sample"]
    with pytest.raises(ValueError, match="exhausted"):
        studio.act(cid, "approve", snap["signature"])


def test_ui_uses_same_sample_and_batch_gate(app, monkeypatch, configured):
    _at, api = app
    cid = sample_only(api, monkeypatch, configured)
    from streamlit.testing.v1 import AppTest
    from test_app import ROOT

    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=40).run()
    at.selectbox(key="active_campaign").select(cid).run()
    at.switch_page("app_pages/plan.py").run()
    snap = api.snapshot(cid)
    assert not at.exception
    assert not at.button(key="approve_plan_" + snap["signature"]).disabled
    perform(api, cid, "approve")
    at.run()
    snap = api.snapshot(cid)
    assert not at.button(key="generate_" + snap["signature"]).disabled
    perform(api, cid, "generate")
    at.switch_page("app_pages/videos.py").run()
    snap = api.snapshot(cid)
    at.checkbox(key="media_checked_" + snap["signature"]).check().run()
    assert at.button(key="approve_sample_" + snap["signature"]).disabled
    assert any("batches are disabled" in w.value for w in at.warning)


def test_completed_clip_is_available_before_sample_export_and_keeps_job_id(
    studio, monkeypatch, configured
):
    cid = sample_only(studio, monkeypatch, configured)
    snap = studio.snapshot(cid)
    from video_agent.generation_contract import generation_key

    key = generation_key(
        snap["state"]["plan"]["ads"][0]["scenes"][0], snap["state"]["brief"]
    )
    folder = studio.repository.folder(cid) / "cache/generation" / key
    folder.mkdir(parents=True)
    clip = folder / "generated.webm"
    clip.write_bytes(b"protocol fixture; not inference evidence")
    save_json(
        folder / "cache.json",
        dict(
            result=dict(
                path=str(clip),
                source="generated footage",
                provider_id="original-comfy-job",
                workflow_sha256="workflow-hash",
            ),
            hashes={str(clip): file_hash(clip)},
        ),
    )
    sources = studio.generation_previews(cid)
    assert sources[0]["provider_id"] == "original-comfy-job" and sources[0][
        "path"
    ] == str(clip)
    assert not studio.snapshot(cid)["state"].get("sample")


def test_raw_preview_displays_provenance_without_submitting(app, tmp_path):
    at, api = app
    cid = at.session_state["active_campaign"]
    state = api.snapshot(cid)["state"]
    clip = tmp_path / "source.webm"
    clip.write_bytes(b"UI fixture only")
    sample = copy.deepcopy(state["sample"])
    sample["visual_source"] = dict(
        scenes=[
            dict(
                source="generated footage",
                path=str(clip),
                provider_id="original-job-123",
                workflow_name="measured-workflow.json",
                workflow_sha256="abc",
                models=["example.gguf"],
                settings=dict(frames=9, fps=24),
            )
        ]
    )
    api.runtime(cid).agent.update_state(api.runtime(cid).config, dict(sample=sample))
    at.switch_page("app_pages/videos.py").run()
    assert not at.exception
    assert any(e.label == "Generated clip · Scene 1" for e in at.expander)
    assert any("original-job-123" in c.value for c in at.caption)
    assert any("example.gguf" in c.value for c in at.caption)
    assert len(at.get("video")) == 2
    assert not api.repository.control.tasks(cid)
