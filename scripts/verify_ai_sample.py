"""One real image-conditioned sample through application approvals and FFmpeg.

Explicit simulated technical-test approvals only; no paid providers or downloads.
Earlier completed submissions stay recoverable in the campaign if interrupted.
"""

import argparse
import copy
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import urlparse

import imageio_ffmpeg
import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from video_agent.cache import file_hash
from video_agent.config import Settings
from video_agent.models import AdPlan
from video_agent.rendering.editor import FFmpegEditor
from video_agent.service import CampaignService
from video_agent.storage import read_json, save_json


def frame(path, filters="scale=64:64"):
    raw = subprocess.check_output(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-v",
            "error",
            "-ss",
            "1",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            filters,
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return np.frombuffer(raw, dtype=np.uint8).astype(np.float32)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate-approvals", action="store_true", required=True)
    parser.add_argument("--main-image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-empty-setup", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.resume_empty_setup:
        raise ValueError(
            "Use a fresh output folder; recover existing jobs through the application, never blindly resubmit."
        )
    original = requests.sessions.Session.request

    def local_only(session, method, url, *a, **kw):
        if urlparse(url).hostname not in ["127.0.0.1", "localhost", "::1"]:
            raise RuntimeError(
                "Nonlocal provider/download blocked in live local verification"
            )
        return original(session, method, url, *a, **kw)

    requests.sessions.Session.request = local_only
    api = CampaignService(Settings(data_dir=args.output.resolve()))
    if args.resume_empty_setup:
        assert not api.repository.list(), (
            "Only an empty setup directory can resume; recover existing campaigns through the app"
        )
        with api.repository.control.transaction() as db:
            assert not db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            assert not db.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    report = dict(
        status="running",
        approvals="Simulated technical workflow approval; human appearance approval still required",
        events=[],
        paid_provider_requests=0,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    cid = None
    try:
        cid = api.create(
            "AI sample verification",
            "Students and professionals",
            "Explore the product",
            product="Trail Bottle",
            brand="Summit",
            creation_mode="combine",
            product_image=(args.main_image.name, args.main_image.read_bytes()),
            limits={"generation_jobs": 1, "render_seconds": 600},
        )
        report["campaign_id"] = cid
        snapshot = api.snapshot(cid)
        plan = copy.deepcopy(snapshot["state"]["plan"])
        plan["ads"][0]["scenes"][0]["edit"]["seed"] = (
            secrets.randbelow(1_000_000_000) + 10000
        )
        api.save_manual_plan(cid, plan, snapshot["signature"])
        for action in ["approve", "generate"]:
            snapshot = api.snapshot(cid)
            report["events"].append(
                dict(
                    stage=snapshot["stage"],
                    action=action,
                    version=snapshot["state"]["version"],
                )
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
            snapshot = api.snapshot(cid)
            if snapshot["job"]["status"] != "finished":
                raise RuntimeError(snapshot["job"]["error"])
        assert snapshot["stage"] == "approve_sample"
        sample = snapshot["state"]["sample"]
        source = sample["visual_source"]["scenes"][0]
        tasks = [
            t for t in api.repository.control.tasks(cid) if t["kind"] == "generation"
        ]
        assert len(tasks) == 1 and tasks[0]["status"] == "complete"
        assert (
            source["source"] == "generated footage"
            and source["provider_id"] == tasks[0]["provider_id"]
        )
        assert all(
            s["source"] == "image animation"
            for s in sample["visual_source"]["scenes"][1:]
        )
        manifest = read_json(sample["source_manifest"])
        assert manifest["scene_sources"][0]["path"] == source["path"]
        renderer = api.runtime(cid).renderer
        canonical = AdPlan.model_validate(
            snapshot["state"]["plan"]["ads"][0]
        ).model_dump()
        scene = canonical["scenes"][0]
        key = FFmpegEditor(renderer, None).scene_key(scene, snapshot["state"]["brief"])
        scene_folder = api.repository.folder(cid) / "cache/scene" / key
        selection_error = float(
            np.mean(
                np.abs(frame(source["path"]) - frame(scene_folder / "selection.mp4"))
            )
        )
        edit = scene["edit"]
        boxw, boxh = (
            round(1080 * edit["width"]) // 2 * 2,
            round(1920 * edit["height"]) // 2 * 2,
        )
        crop = f"crop={boxw}:{boxh}:{round(1080 * edit['x'])}:{round(1920 * edit['y'])},scale=64:64"
        export_error = float(
            np.mean(
                np.abs(
                    frame(sample["video"], crop)
                    - frame(scene_folder / "scene.mp4", crop)
                )
            )
        )
        assert selection_error < 10 and export_error < 5, (
            selection_error,
            export_error,
        )
        # The retained source and the raw-preview API must refer to the SAME original job.
        previews = api.generation_previews(cid)
        assert previews[0]["provider_id"] == source["provider_id"]
        profile = api.runtime(cid).renderer.generation_adapter.profile()
        report.update(
            status="passed",
            sample=sample,
            generated_clip=source,
            profile=profile,
            source_sha256=file_hash(source["path"]),
            pixel_checks=dict(
                raw_to_ffmpeg_selection_mean_absolute_error=selection_error,
                assembled_scene_to_final_export_mean_absolute_error=export_error,
            ),
            generated_scenes=1,
            image_animation_scenes=4,
            task=tasks[0],
            usage=api.repository.control.usage(cid),
            raw_preview_original_job_id=True,
        )
        assert all(u["status"] == "settled" for u in report["usage"])
        print(
            "Actual AI clip is playable and used in the sample export: "
            + source["provider_id"],
            flush=True,
        )
    except BaseException as error:
        report.update(status="blocked_or_failed", error=str(error))
        if cid:
            report["tasks"] = api.repository.control.tasks(cid)
        raise
    finally:
        save_json(args.output / "ai_sample_report.json", report)
        api.close()
        requests.sessions.Session.request = original


if __name__ == "__main__":
    main()
