"""Real local demo verification in an isolated directory, with explicit test approvals.

This never resumes an existing campaign or supplies human approval for real ads.
"""

import argparse
from io import BytesIO
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import requests
import soundfile as sf
from PIL import Image, ImageDraw
from video_agent.config import Settings, MODEL_FILES
from video_agent.service import CampaignService
from video_agent.cache import file_hash
from video_agent.storage import save_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate-approvals", action="store_true", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tmp" / ("local-demo-" + uuid.uuid4().hex[:8]),
    )
    parser.add_argument("--main-image", type=Path)
    parser.add_argument("--detail-image", type=Path)
    parser.add_argument("--product", default="Trail Bottle")
    parser.add_argument("--brand", default="Summit")
    parser.add_argument("--fact", action="append", default=[])
    parser.add_argument(
        "--resume-exported",
        action="store_true",
        help="Recover this verifier's completed initial export; never resubmit its videos",
    )
    args = parser.parse_args()
    settings = Settings(data_dir=args.output.resolve())
    for name, size in MODEL_FILES.items():
        if (
            not (settings.model_dir / name).is_file()
            or (settings.model_dir / name).stat().st_size != size
        ):
            raise SystemExit(
                "Cache the local Kokoro models through an explicit generation request first. This verification script performs no downloads."
            )

    def upload(path, detail=False):
        if path:
            return path.name, path.read_bytes()
        image = Image.new("RGB", (600, 800), "#edf1f4")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((190, 140, 410, 730), radius=55, fill="#1854c4")
        draw.rounded_rectangle((225, 60, 375, 170), radius=20, fill="#132f60")
        if detail:
            image = image.crop((170, 40, 430, 440)).resize((600, 800))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return "synthetic-product.png", buffer.getvalue()

    # No live credits or downloads, including while Kokoro runs.
    def reject_network(*args, **kwargs):
        raise RuntimeError("Offline demo verification attempted a Python HTTP request.")

    requests.sessions.Session.request = reject_network
    api = CampaignService(settings, secrets={}, import_notebook=False)
    report = dict(
        approval_mode="Simulated test approvals, not human advertising approval",
        live_api_requests=0,
        data_directory=str(settings.data_dir),
        events=[],
    )
    try:
        if args.resume_exported:
            from video_agent.storage import read_json

            report = read_json(settings.data_dir / "demo_report.json")
            cid = report["campaign_id"]
            assert report["status"] == "failed"
            report["recovered_error"] = report.pop("error")
        else:
            cid = api.create(
                "Local workflow verification",
                "Young professionals",
                "Explore the product",
                "template",
                product=args.product,
                brand=args.brand,
                claims=args.fact,
                product_image=upload(args.main_image),
                detail_image=upload(args.detail_image, True),
            )
        report["campaign_id"] = cid

        def act(action):
            snap = api.snapshot(cid)
            print("TEST CHECKPOINT:", snap["stage"], "->", action, flush=True)
            report["events"].append(
                dict(stage=snap["stage"], action=action, checkpoint=snap["checkpoint"])
            )
            api.act(cid, action, snap["review_id"])
            api.jobs.wait(cid, timeout=600)
            result = api.snapshot(cid)
            if result["job"]["status"] != "finished":
                raise RuntimeError(result["job"]["error"])
            return result

        if args.resume_exported:
            first = api.snapshot(cid)
        else:
            for action in ["approve", "generate", "approve", "approve"]:
                first = act(action)
        if (
            first["state"]["status"] != "complete"
            or len(first["state"]["outputs"]) != 3
        ):
            raise RuntimeError(
                "The first complete export did not contain three videos."
            )
        initial = first["state"]["outputs"]
        report["initial_outputs"] = initial
        first_export = first["state"]["export_path"]
        counts = {t["key"]: t["attempts"] for t in api.repository.control.tasks(cid)}
        # Complete campaigns have no live review ID; service still validates the
        # current checkpoint when saving this isolated test's new draft.
        api.edit_timeline(
            cid, 2, "scene 2 move product left; quieter music", first["signature"]
        )
        draft = api.snapshot(cid)
        assert draft["stage"] == "approve_plan" and not draft["approved"]
        try:
            api.act(cid, "generate", first["signature"])
        except ValueError:
            report["stale_generation_rejected"] = True
        else:
            raise AssertionError("A revision bypassed fresh plan approval")
        for action in ["approve", "generate", "approve", "approve"]:
            revised = act(action)
        outputs = revised["state"]["outputs"]
        reused = [
            outputs[i]["cache_hit"]
            and file_hash(initial[i]["video"]) == file_hash(outputs[i]["video"])
            for i in [0, 1]
        ]
        changed = file_hash(initial[2]["video"]) != file_hash(outputs[2]["video"])
        if not all(reused) or not changed:
            raise RuntimeError("Targeted revision failed media-reuse checks.")
        for output in outputs:
            if output["quality"]["status"] != "passed":
                raise RuntimeError("A technical export check failed.")
            for role in ["voice", "music"]:
                samples, rate = sf.read(output[role])
                if (
                    len(samples) < rate
                    or not np.isfinite(samples).all()
                    or np.sqrt(np.mean(samples**2)) < 0.001
                ):
                    raise RuntimeError("Missing/invalid " + role)
        later = api.repository.control.tasks(cid)
        report.update(
            status="passed",
            revised_outputs=outputs,
            initial_export=first_export,
            revised_export=revised["state"]["export_path"],
            unchanged_videos_1_and_2=reused,
            changed_video_3=changed,
            speech_reused=all(
                t["attempts"] == counts.get(t["key"])
                for t in later
                if t["kind"] == "speech"
            ),
            actual_voice="Cached local Kokoro",
            music="Original synthesized instrumental mixed by FFmpeg",
            captions="Segmented ASS burned into MP4, plus SRT",
            human_media_approval="Still required for real use",
        )
        save_json(settings.data_dir / "demo_report.json", report)
        print("PASS:", settings.data_dir / "demo_report.json", flush=True)
    except BaseException as error:
        report.update(status="failed", error=str(error))
        save_json(settings.data_dir / "demo_report.json", report)
        raise
    finally:
        api.close()


if __name__ == "__main__":
    main()
