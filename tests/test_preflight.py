"""Ordering and policy regressions; controlled providers are not inference evidence."""

import json
import uuid

import pytest

from test_generation import configured as configured
from test_workflow import product_upload, perform
from video_agent.config import Settings
from video_agent.service import CampaignService
from video_agent.preflight import generation_preflight
from video_agent.control import AllowanceExceeded
from video_agent.usage import footage_estimate


@pytest.mark.parametrize(
    "failure",
    [
        ModuleNotFoundError("Missing TTS dependency: kokoro_onnx"),
        ValueError("Missing TTS model: voices-v1.0.bin"),
    ],
)
def test_unavailable_voice_submits_zero_footage_and_consumes_zero_allowance(
    configured, tmp_path, monkeypatch, failure
):
    f = configured
    api = CampaignService(
        Settings(data_dir=tmp_path.parent / ("pf-" + uuid.uuid4().hex[:6])),
        secrets=f["keys"],
    )
    monkeypatch.setattr(api, "generation_scope", lambda: f["brief"]["generation"])
    calls = []
    monkeypatch.setattr(
        "video_agent.generation.ComfyFootage.generate",
        lambda *args: calls.append("submission"),
    )
    monkeypatch.setattr(
        "video_agent.generation.ComfyFootage.readiness",
        lambda *args: calls.append("readiness"),
    )
    monkeypatch.setattr(
        "video_agent.preflight.editing_readiness", lambda: dict(available=True)
    )
    try:
        cid = api.create(
            "Preflight ordering",
            "Students",
            "Explore the product",
            product="Bottle",
            product_image=product_upload(),
            creation_mode="combine",
            limits={"generation_jobs": 4},
        )
        perform(api, cid, "approve")
        renderer = api.runtime(cid).renderer

        def missing_voice():
            raise failure

        monkeypatch.setattr(renderer, "notebook_kokoro", missing_voice)
        api.act(cid, "generate", api.snapshot(cid)["signature"])
        api.jobs.wait(cid)
        assert "Missing TTS" in api.jobs.get(cid)["error"]
        assert not calls
        assert not [
            t for t in api.repository.control.tasks(cid) if t["kind"] == "generation"
        ]
        assert not [
            u
            for u in api.repository.control.usage(cid)
            if json.loads(u["reserved"]).get("generation_jobs", 0)
        ]
    finally:
        api.close()


def test_preflight_checks_total_new_job_allowance_without_reserving(configured):
    f = configured
    f["cache"].limits["generation_jobs"] = 0
    with pytest.raises(AllowanceExceeded, match="no footage submitted"):
        generation_preflight(
            f["adapter"], [f["scene"]], f["cache"], f["brief"], "sample"
        )
    assert not f["control"].usage("test")
    assert not f["control"].tasks("test")


def test_entire_batch_allowance_is_checked_before_first_submission(configured):
    f = configured
    other = json.loads(json.dumps(f["scene"]))
    other["edit"]["seed"] += 1
    with pytest.raises(AllowanceExceeded, match="2 new footage jobs"):
        generation_preflight(
            f["adapter"], [f["scene"], other], f["cache"], f["brief"], "batch"
        )
    assert not f["control"].usage("test")


def test_completed_footage_survives_later_assembly_failure(configured, monkeypatch):
    from test_generation import install_transport
    from video_agent.generation_contract import generation_key

    f = configured
    calls = install_transport(f, monkeypatch)
    key = generation_key(f["scene"], f["brief"])
    clip = f["adapter"].generate(f["scene"], key, f["cache"])

    def failed_assembly(folder):
        raise ValueError("Assembly failed after successful footage")

    with pytest.raises(ValueError, match="Assembly failed"):
        f["cache"].run("video", "assembly", failed_assembly, lambda result: None)
    monkeypatch.setattr(
        f["adapter"],
        "readiness",
        lambda: pytest.fail("Cached footage needs no new provider job"),
    )
    generation_preflight(f["adapter"], [f["scene"]], f["cache"], f["brief"], "sample")
    assert f["adapter"].generate(f["scene"], key, f["cache"]) == clip
    assert calls["submissions"] == 1
    assert all(row["status"] == "settled" for row in f["control"].usage("test"))


def test_cpu_profile_rejects_batch_before_readiness_or_submission(
    configured, monkeypatch
):
    f = configured
    f["brief"]["generation"]["batch_enabled"] = False
    monkeypatch.setattr(f["adapter"], "scope", lambda: f["brief"]["generation"])
    monkeypatch.setattr(
        f["adapter"],
        "readiness",
        lambda: pytest.fail("Batch gate must precede provider work"),
    )
    with pytest.raises(ValueError, match="one sample scene"):
        generation_preflight(
            f["adapter"], [f["scene"]], f["cache"], f["brief"], "batch"
        )


def test_estimate_separates_native_clip_from_final_export(configured):
    from video_agent.campaigns import template_plan

    data = footage_estimate(template_plan(configured["brief"])["ads"])
    assert data["generated_scenes"] == 3 and data["reused_scenes"] == 12
    assert data["sample_jobs"] == 1 and data["remaining_jobs"] == 2
    assert all(s["native_seconds"] == 0.375 and s["loops"] for s in data["clips"])
    assert all(s["native_seconds"] < s["scene_seconds"] for s in data["clips"])


def test_setup_refuses_unowned_existing_environment(tmp_path):
    from scripts.setup_tts import setup
    import os

    environment = tmp_path / "existing"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"unrelated environment")
    with pytest.raises(ValueError, match="not owned"):
        setup(environment)
    assert python.read_bytes() == b"unrelated environment"


def test_missing_speech_environment_has_setup_command(tmp_path, monkeypatch):
    from video_agent.rendering.voice import local_voice

    monkeypatch.setattr(
        "video_agent.rendering.voice.tts_python", lambda keys: tmp_path / "missing.exe"
    )
    with pytest.raises(ValueError, match="setup_tts.py"):
        local_voice(tmp_path)
