"""Controlled protocol/policy regressions, NOT proof of model inference.

Real inference evidence is produced separately by verify_comfy_workflow.py.
"""

import copy
import json
from pathlib import Path
import pytest
import requests
from video_agent.cache import MediaCache, file_hash
from video_agent.control import ControlStore, DEFAULT_LIMITS, AllowanceExceeded
from video_agent.generation import ComfyFootage
from video_agent.generation_contract import generation_key
from video_agent.storage import save_json
from video_agent.timeline import FootageSettings, ad_key
from video_agent.campaigns import build_brief, template_plan
from video_agent.models import CampaignPlan
from video_agent.validation import plan_copy_issues


class Response:
    def __init__(self, data=None, status=200, content=b"protocol fixture only"):
        self.data, self.status_code, self.content = data, status, content

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def iter_content(self, size):
        yield self.content


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from test_workflow import product_upload

    workflow = tmp_path / "workflow.json"
    workflow.write_bytes(
        (
            Path(__file__).resolve().parents[1] / "workflows/ltx-2b-gguf-i2v.json"
        ).read_bytes()
    )
    weight, clip = tmp_path / "weight.bin", tmp_path / "benchmark.bin"
    weight.write_bytes(b"unit weights")
    clip.write_bytes(b"unit benchmark, not inference")
    spec = FootageSettings(frames=9).model_dump()
    stats = dict(
        system=dict(
            argv=["main.py"],
            ram_total=16 * 1024**3,
            ram_free=8 * 1024**3,
            comfyui_version="test",
            pytorch_version="test",
        ),
        devices=[
            dict(
                type="cuda",
                name="test RTX 4050",
                vram_total=6 * 1024**3,
                vram_free=5 * 1024**3,
            )
        ],
    )
    report = tmp_path / "benchmark.json"
    proof = dict(
        status="complete",
        full_video_decode=True,
        device="cuda",
        workflow_sha256=file_hash(workflow),
        clip=str(clip),
        clip_sha256=file_hash(clip),
        settings=spec,
        system=stats,
    )
    save_json(report, proof)
    profile = dict(
        device="cuda",
        device_name="test RTX 4050",
        max_concurrency=1,
        batch_enabled=True,
        workflow_sha256=file_hash(workflow),
        default_settings=spec,
        tested_settings=[spec],
        minimum_ram_gb=8,
        minimum_available_ram_gb=2,
        minimum_vram_gb=3.5,
        minimum_free_vram_gb=1,
        benchmarks=[dict(report=str(report), sha256=file_hash(report))],
        models=[dict(path=str(weight), sha256=file_hash(weight))],
    )
    profile_path = tmp_path / "profile.json"
    save_json(profile_path, profile)
    image = tmp_path / "product.png"
    image.write_bytes(product_upload()[1])
    assets = {
        "product_main": dict(path=str(image), use="identity", sha256=file_hash(image))
    }
    keys = dict(
        COMFY_WORKFLOW=str(workflow),
        COMFY_PROFILE=str(profile_path),
        COMFY_DEVICE="cuda",
    )
    control = ControlStore(tmp_path / "control.sqlite")
    brief = build_brief(
        "Bottle", "Students", "Explore the product", creation_mode="combine"
    )
    brief.update(
        asset_ids=["product_main", "product_detail"],
        asset_hashes={"product_main": file_hash(image)},
    )
    adapter = ComfyFootage(keys, control, "test", assets=assets, brief=brief)
    brief["generation"] = adapter.scope()
    scene = template_plan(brief)["ads"][0]["scenes"][0]
    cache = MediaCache(
        tmp_path / "cache", control, "test", dict(DEFAULT_LIMITS, generation_jobs=1)
    )
    nodes = {
        n["class_type"]: dict(input=dict(required={}))
        for n in json.loads(workflow.read_text()).values()
    }
    monkeypatch.setattr(
        adapter,
        "get",
        lambda route, **kwargs: stats if route == "/system_stats" else nodes,
    )
    return dict(
        adapter=adapter,
        control=control,
        cache=cache,
        scene=scene,
        brief=brief,
        keys=keys,
        stats=stats,
        nodes=nodes,
        profile=profile,
        profile_path=profile_path,
        report=report,
        proof=proof,
        image=image,
    )


def install_transport(fixture, monkeypatch, *, lose_poll=False, rejected=0):
    adapter = fixture["adapter"]
    calls = dict(submissions=0, uploads=0, downloads=0)

    def post(url, **kwargs):
        if url.endswith("/upload/image"):
            calls["uploads"] += 1
            assert kwargs["files"]["image"][1].read() == fixture["image"].read_bytes()
            return Response(dict(name="condition.png", subfolder="cfc"))
        assert url.endswith("/prompt")
        calls["submissions"] += 1
        if calls["submissions"] <= rejected:
            return Response(dict(node_errors={"7": "invalid"}), 400)
        graph = kwargs["json"]["prompt"]
        assert graph["6"]["inputs"]["image"] == "cfc/condition.png"
        assert graph["7"]["inputs"]["image"] == ["6", 0]
        assert graph["10"]["inputs"]["latent_image"] == ["7", 2]
        return Response(dict(prompt_id="known-id"))

    monkeypatch.setattr("video_agent.generation.requests.post", post)

    def download(*args, **kwargs):
        calls["downloads"] += 1
        return Response()

    monkeypatch.setattr("video_agent.generation.requests.get", download)
    initial_get = adapter.get
    lost = []

    def get(route, **kwargs):
        if route.startswith("/history/"):
            if lose_poll and not lost:
                lost.append(1)
                raise requests.Timeout("poll disconnected")
            return {
                "known-id": dict(
                    status=dict(completed=True, status_str="success"),
                    outputs={
                        "12": dict(
                            images=[
                                dict(
                                    filename="result.webm", type="output", subfolder=""
                                )
                            ]
                        )
                    },
                )
            }
        return initial_get(route, **kwargs)

    monkeypatch.setattr(adapter, "get", get)
    monkeypatch.setattr(
        adapter, "verify_clip", lambda *args: dict(format=dict(duration="0.375"))
    )
    return calls


def test_real_binding_contract_and_verified_gpu_capacity(configured, monkeypatch):
    f = configured
    a = f["adapter"]
    assert a.readiness()["available"]
    calls = install_transport(f, monkeypatch)
    graph, sha = a.build_workflow(f["scene"])
    assert sha == file_hash(f["image"])
    assert graph["3"]["inputs"]["text"] == f["scene"]["edit"]["generation_prompt"]
    assert graph["10"]["inputs"]["noise_seed"] == f["scene"]["edit"]["seed"]
    assert graph["7"]["inputs"]["length"] == 9
    assert calls["submissions"] == 0
    a.keys["COMFY_MIN_VRAM_GB"] = "0"
    f["profile"]["minimum_vram_gb"] = 7
    save_json(f["profile_path"], f["profile"])
    assert not a.readiness()[
        "available"
    ]  # Lowering the legacy threshold never establishes compatibility.


def test_cpu_uses_cpu_flag_and_ram_not_gpu_threshold(configured):
    f = configured
    a = f["adapter"]
    a.device = "cpu"
    a.keys["COMFY_DEVICE"] = "cpu"
    a.keys["COMFY_MIN_VRAM_GB"] = "100"
    f["stats"]["system"]["argv"] = ["main.py", "--cpu"]
    f["stats"]["devices"] = [dict(type="cpu", name="cpu", vram_total=0, vram_free=0)]
    f["proof"].update(device="cpu", system=f["stats"])
    save_json(f["report"], f["proof"])
    f["profile"].update(
        device="cpu",
        benchmarks=[dict(report=str(f["report"]), sha256=file_hash(f["report"]))],
    )
    save_json(f["profile_path"], f["profile"])
    assert a.readiness()["available"]
    f["stats"]["system"]["argv"] = ["main.py", "--lowvram"]
    assert not a.readiness()["available"]
    f["stats"]["system"]["argv"] = ["main.py", "--cpu"]
    f["stats"]["system"]["ram_free"] = 1024
    assert "RAM" in a.readiness()["message"]


def test_unused_image_loader_is_not_conditioning(configured):
    f = configured
    graph = json.loads(Path(f["keys"]["COMFY_WORKFLOW"]).read_text())
    graph["7"]["inputs"]["image"] = ["unused", 0]
    with pytest.raises(ValueError, match="condition"):
        f["adapter"].validate_workflow(graph, f["nodes"])


def test_conditioning_bytes_must_match_the_approved_image(configured, monkeypatch):
    f = configured
    install_transport(f, monkeypatch)
    f["image"].write_bytes(b"changed")
    with pytest.raises(ValueError, match="fresh plan approval"):
        f["adapter"].build_workflow(f["scene"])


@pytest.mark.parametrize("lose_poll", [False, True])
def test_known_submission_recovers_and_reuses_without_another_job(
    configured, monkeypatch, lose_poll
):
    f = configured
    calls = install_transport(f, monkeypatch, lose_poll=lose_poll)
    a = f["adapter"]
    key = generation_key(f["scene"], f["brief"])
    if lose_poll:
        with pytest.raises(requests.Timeout):
            a.generate(f["scene"], key, f["cache"])
        assert f["control"].task("test", "generation:" + key)["status"] == "waiting"
    path = a.generate(f["scene"], key, f["cache"])
    assert a.generate(f["scene"], key, f["cache"]) == path
    Path(
        path
    ).unlink()  # A missing download is recovered from the known provider output.
    a.generate(f["scene"], key, f["cache"])
    assert calls["submissions"] == 1
    assert f["control"].task("test", "generation:" + key)["attempts"] == 1
    usage = f["control"].usage("test")
    assert len(usage) == 1 and usage[0]["status"] == "settled"
    assert json.loads(usage[0]["actual"])["generation_jobs"] == 1


def test_rejected_graph_releases_reservation_then_bounded_retry(
    configured, monkeypatch
):
    f = configured
    calls = install_transport(f, monkeypatch, rejected=1)
    key = generation_key(f["scene"], f["brief"])
    with pytest.raises(ValueError, match="rejected"):
        f["adapter"].generate(f["scene"], key, f["cache"])
    assert f["control"].task("test", "generation:" + key)["status"] == "failed"
    assert json.loads(f["control"].usage("test")[0]["actual"])["generation_jobs"] == 0
    f["adapter"].generate(f["scene"], key, f["cache"])
    assert calls["submissions"] == 2
    assert all(u["status"] == "settled" for u in f["control"].usage("test"))


def test_zero_allowance_stops_before_upload_or_submission(configured, monkeypatch):
    f = configured
    calls = install_transport(f, monkeypatch)
    f["cache"].limits["generation_jobs"] = 0
    with pytest.raises(AllowanceExceeded):
        f["adapter"].generate(f["scene"], "blocked", f["cache"])
    assert calls["submissions"] == calls["uploads"] == 0


def test_invalid_download_recovers_same_job_without_duplicate_generation(
    configured, monkeypatch
):
    f = configured
    calls = install_transport(f, monkeypatch)
    adapter = f["adapter"]
    valid_verify = adapter.verify_clip
    monkeypatch.setattr(
        adapter,
        "verify_clip",
        lambda *args: (_ for _ in ()).throw(ValueError("truncated download")),
    )
    with pytest.raises(ValueError, match="same provider result"):
        adapter.generate(f["scene"], "bad-download", f["cache"])
    task = f["control"].task("test", "generation:bad-download")
    assert task["status"] == "waiting" and task["provider_id"] == "known-id"
    monkeypatch.setattr(adapter, "verify_clip", valid_verify)
    adapter.generate(f["scene"], "bad-download", f["cache"])
    assert calls == dict(submissions=1, uploads=1, downloads=2)
    usage = f["control"].usage("test")
    assert len(usage) == 1 and usage[0]["status"] == "settled"
    assert json.loads(usage[0]["actual"])["generation_jobs"] == 1


def test_creation_plans_and_media_dependencies(configured):
    f = configured
    b = f["brief"]
    plan = template_plan(b)
    assert not plan_copy_issues(CampaignPlan.model_validate(plan), b)
    assert (
        sum(s["edit"]["mode"] != "reused" for a in plan["ads"] for s in a["scenes"])
        == 3
    )
    before = generation_key(plan["ads"][0]["scenes"][0], b)
    changed = copy.deepcopy(plan["ads"][0]["scenes"][0])
    changed["headline"] = "A different hook"
    assert generation_key(changed, b) == before
    changed["edit"]["seed"] += 1
    assert generation_key(changed, b) != before
    policy = copy.deepcopy(b)
    policy["generation"]["batch_enabled"] = False
    assert generation_key(plan["ads"][0]["scenes"][0], policy) == before
    assert ad_key(plan["ads"][0], policy) == ad_key(plan["ads"][0], b)
    generated = copy.deepcopy(b)
    generated["creation_mode"] = "generate"
    assert (
        sum(
            s["edit"]["mode"] != "reused"
            for a in template_plan(generated)["ads"]
            for s in a["scenes"]
        )
        == 15
    )


def test_new_settings_do_not_invalidate_legacy_editing_cache():
    brief = build_brief(
        "Trail Bottle",
        "Hikers",
        "Explore the product",
        brand="Summit",
        claims=["750 ml capacity.", "Stainless steel body."],
    )
    brief.update(
        asset_ids=["product_main", "product_detail"],
        asset_hashes={"product_main": "abc", "product_detail": "def"},
    )
    assert [ad_key(ad, brief) for ad in template_plan(brief)["ads"]] == [
        "652159c26b47f9d60b96075355d43a94",
        "46677fdcede2ad4938fc398f720638d9",
        "2f4f03335c92d096dd7808612c945e60",
    ]


def test_known_rejections_have_a_bounded_retry_count(configured, monkeypatch):
    f = configured
    calls = install_transport(f, monkeypatch, rejected=3)
    for _ in range(2):
        with pytest.raises(ValueError, match="rejected"):
            f["adapter"].generate(f["scene"], "rejections", f["cache"])
    with pytest.raises(RuntimeError, match="retry limit"):
        f["adapter"].generate(f["scene"], "rejections", f["cache"])
    assert calls["submissions"] == 2
    assert all(u["status"] == "settled" for u in f["control"].usage("test"))


def test_cancellation_does_not_claim_running_inference_stopped(configured, monkeypatch):
    f = configured
    calls = install_transport(f, monkeypatch)
    a = f["adapter"]
    completed_get = a.get

    def cancelled_poll(route, **kwargs):
        if route.startswith("/history/"):
            f["control"].cancel("test")
            return {}
        if route == "/queue":
            return dict(queue_running=[[1, "known-id"]], queue_pending=[])
        return completed_get(route, **kwargs)

    monkeypatch.setattr(a, "get", cancelled_poll)
    monkeypatch.setattr("video_agent.generation.time.sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="Cancelled"):
        a.generate(f["scene"], "cancelled", f["cache"])
    task = f["control"].task("test", "generation:cancelled")
    assert task["status"] == "waiting" and task["provider_id"] == "known-id"
    f["control"].cancel("test", False)
    monkeypatch.setattr(a, "get", completed_get)
    a.generate(f["scene"], "cancelled", f["cache"])
    assert calls["submissions"] == 1


def test_higher_server_allowance_requires_a_new_plan_review(configured, tmp_path):
    from video_agent.config import Settings
    from video_agent.service import CampaignService
    from test_workflow import make_campaign, perform

    f = configured
    api = CampaignService(
        Settings(data_dir=tmp_path / "campaigns"),
        secrets=dict(f["keys"], CFC_DEFAULT_LIMITS_JSON='{"generation_jobs":4}'),
    )
    try:
        cid = make_campaign(
            api,
            creation_mode="combine",
            limits=dict(DEFAULT_LIMITS, model_tokens=77777),
        )
        before = perform(api, cid, "approve")
        api.review_server_generation_allowance(cid, before["signature"])
        after = api.snapshot(cid)
        assert after["stage"] == "approve_plan" and not after["approved"]
        assert after["state"]["brief"]["limits"]["generation_jobs"] == 4
        assert after["state"]["brief"]["limits"]["model_tokens"] == 77777
        assert (
            after["state"]["brief"]["usage_rates"]
            == before["state"]["brief"]["usage_rates"]
        )
        assert ad_key(
            after["state"]["plan"]["ads"][0], after["state"]["brief"]
        ) == ad_key(before["state"]["plan"]["ads"][0], before["state"]["brief"])
        with pytest.raises(ValueError, match="changed"):
            api.act(cid, "generate", before["signature"])
        assert not api.repository.control.tasks(cid)
    finally:
        api.close()


def test_owned_pending_cancellation_is_confirmed_and_reservation_settled(
    configured, monkeypatch
):
    f = configured
    c = f["control"]
    a = f["adapter"]
    key = "generation:queued"
    c.task_start("test", key, "generation")
    c.task_update("test", key, "queued", provider_id="owned")
    c.reserve("test", key + ":1", {"generation_jobs": 1}, f["cache"].limits)
    reads = []
    posts = []

    def get(route, **kwargs):
        if route == "/queue":
            reads.append(1)
            return dict(
                queue_running=[],
                queue_pending=[[1, "other"]]
                + ([[2, "owned"]] if len(reads) == 1 else []),
            )
        return {}

    monkeypatch.setattr(a, "get", get)
    monkeypatch.setattr(
        "video_agent.generation.requests.post",
        lambda url, **kwargs: posts.append((url, kwargs)) or Response(),
    )
    assert a.cancel_queued() == ["owned"]
    assert (
        len(posts) == 1
        and posts[0][0].endswith("/queue")
        and posts[0][1]["json"] == {"delete": ["owned"]}
    )
    assert c.task("test", key)["status"] == "cancelled"
    assert c.usage("test")[0]["status"] == "settled"
