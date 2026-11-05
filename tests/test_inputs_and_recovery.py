from pathlib import Path
import shutil
import pytest
import requests
from video_agent.control import ControlStore, DEFAULT_LIMITS, AllowanceExceeded
from video_agent.cache import MediaCache
from video_agent.config import Settings
from video_agent.service import CampaignService
from video_agent.inputs import InputAnalyzer
from video_agent.generation import ComfyFootage
from video_agent.rendering.editor import run_ffmpeg
from video_agent.storage import save_json
from test_workflow import studio as studio, make_campaign, perform


def test_review_and_verified_cache_survive_restart(tmp_path):
    settings = Settings(data_dir=tmp_path / "studio")
    api = CampaignService(settings)
    cid = make_campaign(api)
    review = api.snapshot(cid)["signature"]
    api.close()
    api = CampaignService(settings)
    try:
        assert api.snapshot(cid)["signature"] == review
        perform(api, cid, "approve")
        assert api.snapshot(cid)["approved"]
    finally:
        api.close()
    store = ControlStore(tmp_path / "cache-control.sqlite")
    cache = MediaCache(tmp_path / "cache", store, "test", DEFAULT_LIMITS)
    builds = []

    def build(folder):
        builds.append(True)
        path = folder / "result.bin"
        path.write_bytes(b"verified")
        return dict(path=str(path), _files=[path])

    def verify(result):
        if Path(result["path"]).read_bytes() != b"verified":
            raise ValueError("corrupt")

    original, hit = cache.run("scene", "one", build, verify)
    restored = MediaCache(
        tmp_path / "cache",
        ControlStore(tmp_path / "cache-control.sqlite"),
        "test",
        DEFAULT_LIMITS,
    )
    assert restored.run("scene", "one", build, verify)[1]
    assert len(builds) == 1
    Path(original["path"]).write_bytes(b"corrupt")
    assert not restored.run("scene", "one", build, verify)[1]
    assert len(builds) == 2


def test_expired_worker_lease_and_unknown_submission_are_not_blindly_retried(tmp_path):
    store = ControlStore(tmp_path / "control.sqlite")
    store.acquire("campaign", "lost-worker")
    assert store.busy("campaign")
    with store.transaction() as db:
        db.execute("UPDATE leases SET expires=0")
    restored = ControlStore(tmp_path / "control.sqlite")
    assert not restored.busy("campaign")
    restored.task_start("campaign", "speech:one", "speech")
    restored.task_update(
        "campaign",
        "speech:one",
        "unknown",
        provider_id="recorded-id",
        error="response lost",
    )
    with pytest.raises(RuntimeError, match="reconcile"):
        restored.task_start("campaign", "speech:one", "speech")
    assert restored.task("campaign", "speech:one")["provider_id"] == "recorded-id"


def test_budget_exhaustion_pauses_before_dispatch_and_cancellation_stops_future_tasks(
    tmp_path,
):
    control = ControlStore(tmp_path / "control.sqlite")
    cache = MediaCache(
        tmp_path / "cache", control, "one", dict(DEFAULT_LIMITS, speech_characters=0)
    )

    def forbidden(folder):
        raise AssertionError("Provider was called despite zero allowance")

    with pytest.raises(AllowanceExceeded):
        cache.run(
            "speech",
            "x",
            forbidden,
            lambda r: None,
            {"speech_characters": 10},
            external=True,
        )
    assert control.task("one", "speech:x")["status"] == "paused"
    control.cancel("one")
    with pytest.raises(RuntimeError, match="Cancelled"):
        cache.run("scene", "x", forbidden, lambda r: None)


def test_comfy_submission_timeout_is_persisted_and_reconciled_before_retry(
    tmp_path, monkeypatch
):
    workflow = tmp_path / "workflow.json"
    save_json(workflow, {"1": {"class_type": "UNETLoader", "inputs": {}}})
    control = ControlStore(tmp_path / "control.sqlite")
    cache = MediaCache(
        tmp_path / "cache", control, "one", dict(DEFAULT_LIMITS, generation_jobs=1)
    )
    adapter = ComfyFootage({"COMFY_WORKFLOW": str(workflow)}, control, "one")
    monkeypatch.setattr(adapter, "readiness", lambda: dict(available=True))
    count = []

    def timeout(*args, **kwargs):
        count.append(1)
        raise requests.Timeout("lost response")

    monkeypatch.setattr("video_agent.generation.requests.post", timeout)
    scene = {
        "edit": {
            "generation_prompt": "An abstract background without products",
            "seed": 1,
        }
    }
    with pytest.raises(requests.Timeout):
        adapter.generate(scene, "scene-hash", cache)
    row = control.task("one", "generation:scene-hash")
    assert row["status"] == "unknown" and row["provider_id"].startswith("client:")
    monkeypatch.setattr(adapter, "reconcile", lambda marker: None)
    with pytest.raises(RuntimeError, match="remains unknown"):
        adapter.generate(scene, "scene-hash", cache)
    assert len(count) == 1


@pytest.mark.skipif(
    not shutil.which("ffprobe"), reason="FFprobe needed for local input analysis"
)
def test_video_frames_transcript_and_hash_analysis_cache(studio, tmp_path, monkeypatch):
    cid = make_campaign(studio)
    renderer = studio.runtime(cid).renderer
    video = tmp_path / "reference.mp4"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=240x426:r=10:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=16000:duration=1",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            video,
        ],
        tmp_path,
    )
    analyzer = InputAnalyzer(tmp_path / "analysis", {})
    calls = []

    def transcribe(source, folder):
        calls.append(source)
        return dict(status="transcribed", text="Reference spoken brief")

    monkeypatch.setattr(analyzer, "transcribe", transcribe)
    result = analyzer.analyze(video, "video", renderer.probe)
    assert len(result["frames"]) == 4 and all(
        Path(p).is_file() for p in result["frames"]
    )
    assert result["transcript"]["text"] == "Reference spoken brief"
    assert (
        analyzer.analyze(video, "video", renderer.probe)["cache_hit"]
        and len(calls) == 1
    )


def test_incomplete_brief_and_text_analysis_reopen_review(studio):
    cid = make_campaign(studio, product_image=None)
    before = studio.snapshot(cid)
    assert before["stage"] == "approve_plan" and before["missing_assets"]
    studio.add_input(
        cid,
        "brief.txt",
        b"Use a calm, simple presentation.",
        "text",
        before["signature"],
    )
    studio.jobs.wait(cid)
    after = studio.snapshot(cid)
    assert (
        after["signature"] != before["signature"] and after["stage"] == "approve_plan"
    )
    assert after["state"]["brief"]["input_analysis"][0]["text"].startswith("Use a calm")


def test_new_input_preserves_prior_outputs(studio):
    from test_workflow import stub_runtime

    cid = make_campaign(studio)
    stub_runtime(studio, cid)
    perform(studio, cid, "approve")
    before = perform(studio, cid, "generate")
    video = before["state"]["sample"]["video"]
    studio.add_input(cid, "brief.txt", b"A revised brief.", "text", before["signature"])
    studio.jobs.wait(cid)
    after = studio.snapshot(cid)
    assert after["stage"] == "approve_plan" and not after["approved"]
    assert any(item["video"] == video for item in after["state"]["prior_outputs"])
    assert Path(video).is_file()


def test_manual_transcript_fallback_with_missing_whisper_and_relative_folder(
    studio, tmp_path, monkeypatch
):
    from video_agent.inputs import InputAnalyzer

    cid = make_campaign(studio)
    # Explicit missing settings must override any developer-machine installation.
    analyzer = InputAnalyzer(
        tmp_path / "analysis",
        {
            "WHISPER_CPP_BIN": str(tmp_path / "missing.exe"),
            "WHISPER_CPP_MODEL": str(tmp_path / "missing.bin"),
        },
    )
    result = analyzer.transcribe(tmp_path / "recording.wav", tmp_path / "analysis")
    assert result["status"] == "unavailable" and "manually" in result["message"]
    before = studio.snapshot(cid)
    studio.add_input(
        cid,
        "manual-transcript.txt",
        b"A manually corrected spoken brief.",
        "text",
        before["signature"],
    )
    studio.jobs.wait(cid)
    analysis = studio.snapshot(cid)["state"]["brief"]["input_analysis"][0]
    assert analysis["text"] == "A manually corrected spoken brief."
    monkeypatch.chdir(tmp_path)
    assert (
        InputAnalyzer("relative-analysis", {}).folder == tmp_path / "relative-analysis"
    )


def test_research_cache_expiry_deduplication_and_real_refresh(studio, monkeypatch):
    cid = make_campaign(studio)
    planner = studio.runtime(cid).planner
    calls = []

    def search(name):
        calls.append(name)
        return {
            "results": [
                {
                    "url": "https://example.test/products/item",
                    "title": "Made for travel",
                    "content": "Explore the compact collection. Learn more.",
                }
            ]
            * 2
        }

    monkeypatch.setattr(planner, "search_competitor", search)
    first = planner.research_competitors(["Comparable product"])
    planner.research_competitors(["Comparable product"])
    assert len(calls) == 1 and len(first) == 1
    planner.research_competitors(["Comparable product"], refresh=True)
    assert len(calls) == 2
    planner.research_competitors(["Comparable product"], ttl_seconds=-1)
    assert len(calls) == 3
    assert (
        first[0]["reference_type"] == "product_page"
        and first[0]["active_ad_status"] == "unverified"
    )
