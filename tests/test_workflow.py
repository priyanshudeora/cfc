"""Approval behavior with simulated providers and rendering, isolated from campaign files."""

from pathlib import Path
import copy
import json
import threading
import pytest
from io import BytesIO
from PIL import Image
from video_agent.config import Settings
from video_agent.service import CampaignService
from video_agent.storage import read_json, save_json
from video_agent.campaigns import build_brief, template_plan


@pytest.fixture
def studio(tmp_path):
    api = CampaignService(
        Settings(data_dir=tmp_path),
        secrets={"GEMINI_API_KEY": "test-gemini-key"},
        import_notebook=False,
    )
    yield api
    api.close()


def product_upload(color="blue", size=(360, 600), name="product.png"):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return name, buffer.getvalue()


def campaign_inputs(**changes):
    return {
        "product": "Trail Bottle",
        "brand": "Summit",
        "claims": ["750 ml capacity.", "Stainless steel body."],
        "product_image": product_upload(),
        **changes,
    }


def make_campaign(api, **changes):
    return api.create(
        "Review test",
        "Hikers and commuters",
        "Explore the product",
        "template",
        **campaign_inputs(**changes),
    )


def stub_runtime(api, cid):
    runtime = api.runtime(cid)
    rendered = []

    def fake_render(state, ad, repository):
        repository.require_approval(state)
        rendered.append(ad["video_type"])
        folder = repository.folder(cid) / "fake-media" / str(len(rendered))
        folder.mkdir(parents=True)
        (folder / "video.mp4").write_bytes(b"simulated-video-for-flow-test")
        (folder / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")
        return dict(
            title=ad["title"],
            version=state["version"],
            video=str(folder / "video.mp4"),
            captions=str(folder / "captions.srt"),
            voice="",
            quality_check="Simulated renderer",
        )

    def fake_plan(brief, sources, feedback="", previous_plan=None):
        plan = copy.deepcopy(previous_plan or template_plan(brief))
        plan["summary"] = feedback or plan["summary"]
        return plan, "Simulated planner"

    def fake_revision(state, index, feedback):
        plan = copy.deepcopy(state["plan"])
        plan["ads"][index]["title"] = feedback
        return plan

    runtime.renderer.render = fake_render
    runtime.planner.make_plan = fake_plan
    runtime.planner.revise_ad = fake_revision
    return runtime, rendered


def perform(api, cid, action, **kwargs):
    snap = api.snapshot(cid)
    api.act(cid, action, snap["signature"], **kwargs)
    api.jobs.wait(cid)
    result = api.snapshot(cid)
    assert result["job"]["status"] == "finished", result["job"]
    return result


def test_full_review_flow(studio):
    cid = make_campaign(studio, use_footage=True)
    runtime, rendered = stub_runtime(studio, cid)
    assert studio.snapshot(cid)["stage"] == "approve_plan"
    with pytest.raises(ValueError, match="Approve and save"):
        studio.act(cid, "generate", studio.snapshot(cid)["signature"])
    snap = perform(studio, cid, "revise_plan", feedback="Use a more direct opening.")
    assert snap["stage"] == "approve_plan" and not rendered and not snap["approved"]
    snap = perform(studio, cid, "approve")
    assert snap["stage"] == "ready_sample" and snap["approved"] and not rendered
    assert studio.repository.approved_path(snap["state"]).with_suffix(".md").exists()
    snap = perform(studio, cid, "generate")
    assert rendered == ["product_demo"] and snap["stage"] == "approve_sample"
    snap = perform(studio, cid, "approve")
    assert snap["stage"] == "prepare_assets" and len(rendered) == 1
    footage = studio.repository.folder(cid) / "walking.mp4"
    footage.write_bytes(b"fixture")
    save_json(runtime.renderer.commute_config, {"lifestyle_clip": str(footage)})
    snap = perform(studio, cid, "continue")
    assert rendered == ["product_demo", "lifestyle_story", "product_reveal"]
    assert snap["stage"] == "approve_final"
    snap = perform(studio, cid, "revise", feedback="Different reveal opening", index=2)
    assert (
        snap["stage"] == "approve_plan" and len(rendered) == 3 and not snap["approved"]
    )
    for act in ["approve", "generate", "approve", "approve"]:
        snap = perform(studio, cid, act)
    assert snap["state"]["status"] == "complete"
    assert Path(snap["state"]["export_path"]).suffix == ".zip"
    import zipfile

    with zipfile.ZipFile(snap["state"]["export_path"]) as archive:
        assert len([name for name in archive.namelist() if name.endswith(".mp4")]) == 3


def test_stale_approval_manual_edit_and_cancellation(studio):
    cid = make_campaign(studio)
    runtime, rendered = stub_runtime(studio, cid)
    old = studio.snapshot(cid)
    edited = copy.deepcopy(old["state"]["plan"])
    edited["summary"] = "Manually revised campaign direction."
    studio.save_manual_plan(cid, edited, old["signature"])
    with pytest.raises(ValueError, match="changed"):
        studio.act(cid, "approve", old["signature"])
    current = studio.snapshot(cid)
    assert not current["approved"] and not rendered
    invalid = copy.deepcopy(edited)
    invalid["ads"][0]["scenes"][0]["headline"] = "Waterproof ANC earbuds"
    with pytest.raises(ValueError):
        studio.save_manual_plan(cid, invalid, current["signature"])
    snap = perform(studio, cid, "approve")
    approval = studio.repository.approved_path(snap["state"])
    record = read_json(approval)
    record["brief"]["goal"] = "Changed after approval"
    save_json(approval, record)
    with pytest.raises(ValueError, match="differs"):
        studio.repository.require_approval(snap["state"])
    snap = perform(studio, cid, "cancel")
    assert snap["state"]["status"] == "cancelled" and not rendered


def test_failed_save_blocks_media(studio, monkeypatch):
    cid = make_campaign(studio)
    runtime, rendered = stub_runtime(studio, cid)
    original = studio.repository.approve
    monkeypatch.setattr(
        studio.repository,
        "approve",
        lambda state, **kw: (_ for _ in ()).throw(OSError("disk full")),
    )
    studio.act(cid, "approve", studio.snapshot(cid)["signature"])
    studio.jobs.wait(cid)
    snap = studio.snapshot(cid)
    assert (
        snap["stage"] == "save_approved_plan"
        and snap["job"]["status"] == "failed"
        and not rendered
    )
    monkeypatch.setattr(studio.repository, "approve", original)
    snap = perform(studio, cid, "retry")
    assert snap["stage"] == "ready_sample" and not rendered


def test_duplicate_submission_is_rejected(studio):
    cid = make_campaign(studio)
    runtime, rendered = stub_runtime(studio, cid)
    entered, release = threading.Event(), threading.Event()
    original = runtime.planner.make_plan

    def slow_plan(*args, **kwargs):
        entered.set()
        release.wait(10)
        return original(*args, **kwargs)

    runtime.planner.make_plan = slow_plan
    snap = studio.snapshot(cid)
    studio.act(cid, "revise_plan", snap["signature"], feedback="A different hook")
    assert entered.wait(5)
    try:
        with pytest.raises(ValueError, match="Wait"):
            studio.act(cid, "approve", snap["signature"])
    finally:
        release.set()
    studio.jobs.wait(cid)
    assert not rendered


def test_restarts_preserve_pending_review(tmp_path):
    settings = Settings(data_dir=tmp_path)
    api = CampaignService(settings, import_notebook=False)
    cid = make_campaign(api)
    perform(api, cid, "approve")
    api.close()
    restored = CampaignService(settings, import_notebook=False)
    try:
        snap = restored.snapshot(cid)
        assert (
            snap["stage"] == "ready_sample" and snap["interrupted"] and snap["approved"]
        )
    finally:
        restored.close()


def test_ai_plan_uses_structured_response_and_original_images(studio, monkeypatch):
    brief = build_brief(
        "Trail Bottle",
        "Students",
        "Explore the product",
        claims=["750 ml capacity.", "Stainless steel body."],
    )
    fixture = template_plan(brief)
    calls = []

    class Response:
        ok, status_code = True, 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            title = self.payload["response_format"]["schema"]["title"]
            body = (
                fixture
                if title == "CampaignPlan"
                else dict(score=9, issues=[], unsupported_claims=[])
            )
            return {
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": json.dumps(body)}],
                    }
                ]
            }

    def request(method, url, **kwargs):
        if method == "GET":

            class ModelResponse:
                ok, status_code = True, 200

                def json(self):
                    return {"name": "models/gemini-2.5-flash"}

            return ModelResponse()
        calls.append(kwargs["json"])
        return Response(kwargs["json"])

    monkeypatch.setattr("video_agent.connections.requests.request", request)
    cid = studio.create(
        "AI plan test", "Students", "Explore the product", "ai", **campaign_inputs()
    )
    studio.jobs.wait(cid)
    snap = studio.snapshot(cid)
    assert snap["stage"] == "approve_plan" and not snap["approved"]
    assert snap["job"]["status"] == "finished" and not snap["job"]["error"]
    assert len(calls) == 2
    assert len([part for part in calls[0]["input"] if part["type"] == "image"]) == 2
    assert calls[0]["store"] is False


def test_failed_ai_plan_can_use_saved_draft(studio, monkeypatch):
    monkeypatch.setattr(
        "video_agent.planning.Planner.make_plan",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Gemini busy")),
    )
    cid = studio.create(
        "Fallback test", "Students", "Explore the product", "ai", **campaign_inputs()
    )
    studio.jobs.wait(cid)
    snap = studio.snapshot(cid)
    assert snap["job"]["status"] == "failed" and snap["stage"] == "plan"
    studio.use_saved_draft(cid, snap["signature"])
    snap = studio.snapshot(cid)
    assert snap["stage"] == "approve_plan" and not snap["approved"]


def test_manual_editor_rejects_unapproved_numbers_and_prices(studio):
    cid = make_campaign(studio)
    snap = studio.snapshot(cid)
    for headline in ["100 hours playback", "IPX8 protection", "Only ₹999"]:
        plan = copy.deepcopy(snap["state"]["plan"])
        plan["ads"][0]["scenes"][0]["headline"] = headline
        with pytest.raises(ValueError):
            studio.save_manual_plan(cid, plan, snap["signature"])
