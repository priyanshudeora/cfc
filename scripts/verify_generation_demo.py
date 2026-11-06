"""Actual local ComfyUI -> Kokoro -> FFmpeg -> export in an isolated campaign.

All approval receipts here are explicitly simulated test approvals, never human
advertising approval. No paid-provider or model-download HTTP request is allowed.
"""

import argparse
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urlparse
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from video_agent.config import Settings, MODEL_FILES
from video_agent.service import CampaignService
from video_agent.storage import save_json
from video_agent.cache import file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate-approvals", action="store_true", required=True)
    parser.add_argument("--main-image", required=True, type=Path)
    parser.add_argument("--detail-image", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tmp" / ("generated-demo-" + uuid.uuid4().hex[:8]),
    )
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError(
            "Use a fresh isolated output directory; existing campaigns are never overwritten."
        )
    settings = Settings(data_dir=args.output.resolve())
    for name, size in MODEL_FILES.items():
        if (
            not (settings.model_dir / name).is_file()
            or (settings.model_dir / name).stat().st_size != size
        ):
            raise ValueError(
                "Cache Kokoro explicitly before this offline-voice verification."
            )
    original_request = requests.sessions.Session.request

    def local_only(session, method, url, *positional, **keywords):
        parsed = urlparse(url)
        if parsed.hostname not in ["127.0.0.1", "localhost", "::1"]:
            raise RuntimeError(
                "A paid-provider/download HTTP request was blocked in local verification."
            )
        return original_request(session, method, url, *positional, **keywords)

    requests.sessions.Session.request = local_only
    api = CampaignService(settings, import_notebook=False)
    report = dict(
        approval_mode="Simulated technical-test receipts; product appearance NOT approved for advertising",
        external_http_requests=0,
        evidence="Actual local ComfyUI, Kokoro and FFmpeg",
        events=[],
    )
    cid = None

    def perform(action):
        snapshot = api.snapshot(cid)
        report["events"].append(
            dict(action=action, stage=snapshot["stage"], estimate=snapshot["estimate"])
        )
        api.act(cid, action, snapshot["signature"])
        last = ""
        while api.jobs.busy(cid):
            job = api.jobs.get(cid)
            message = job["events"][-1] if job["events"] else job["title"]
            if message != last:
                print(message, flush=True)
                last = message
            time.sleep(2)
        result = api.snapshot(cid)
        if result["job"]["status"] != "finished":
            raise RuntimeError(result["job"]["error"])
        save_json(args.output / "generation_demo_report.json", report)
        return result

    try:
        detail = args.detail_image or args.main_image
        cid = api.create(
            "Generation verification only",
            "Students and young professionals",
            "Encourage viewers to explore the product",
            product="Trail Bottle",
            brand="Summit",
            creation_mode="combine",
            product_image=(args.main_image.name, args.main_image.read_bytes()),
            detail_image=(detail.name, detail.read_bytes()),
            limits={"generation_jobs": 4, "render_seconds": 1500},
        )
        report["campaign_id"] = cid
        report["brief"] = api.snapshot(cid)["state"]["brief"]
        perform("approve")
        sample = perform("generate")
        assert sample["stage"] == "approve_sample"
        report["sample"] = sample["state"]["sample"]
        batch = perform("approve")
        assert batch["stage"] == "approve_final"
        initial = batch["state"]["outputs"]
        report["initial_outputs"] = initial
        first_export = perform("approve")
        assert first_export["state"]["status"] == "complete"
        report["initial_export"] = first_export["state"]["export_path"]
        import copy

        changed = copy.deepcopy(first_export["state"]["plan"])
        changed["ads"][2]["scenes"][0]["edit"]["seed"] += 1
        api.save_manual_plan(cid, changed, first_export["signature"])
        perform("approve")
        perform("generate")
        revised = perform("approve")
        outputs = revised["state"]["outputs"]
        assert [file_hash(v["video"]) for v in initial[:2]] == [
            file_hash(v["video"]) for v in outputs[:2]
        ]
        assert all(v.get("cache_hit") for v in outputs[:2])
        assert file_hash(initial[2]["video"]) != file_hash(outputs[2]["video"])
        final = perform("approve")
        tasks = api.repository.control.tasks(cid)
        generated = [t for t in tasks if t["kind"] == "generation"]
        assert len(generated) == 4 and all(t["status"] == "complete" for t in generated)
        assert all(
            any(
                s["source"] == "generated footage" for s in v["visual_source"]["scenes"]
            )
            for v in outputs
        )
        report.update(
            status="passed",
            final_outputs=outputs,
            final_export=final["state"]["export_path"],
            videos_1_and_2_reused=True,
            generation_tasks=generated,
            usage=api.repository.control.usage(cid),
            human_visual_approval=False,
        )
        save_json(args.output / "generation_demo_report.json", report)
        print(
            "Actual generated footage exported; video 3 revised, videos 1 and 2 reused.",
            flush=True,
        )
        print(final["state"]["export_path"], flush=True)
    finally:
        api.close()
        requests.sessions.Session.request = original_request


if __name__ == "__main__":
    main()
