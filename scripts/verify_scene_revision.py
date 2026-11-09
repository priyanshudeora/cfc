"""Two genuine scene generations with fresh test approvals and unaffected reuse.

Uses ONLY an existing isolated generation-verification campaign. Prior versions
are retained. Approvals are simulated workflow checks, not advertising approval.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import traceback
from urllib.parse import urlparse

import imageio_ffmpeg
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from video_agent.config import Settings
from video_agent.service import CampaignService
from video_agent.storage import read_json, save_json
from video_agent.cache import file_hash
from video_agent.generation_contract import generation_key
from video_agent.rendering.editor import FFmpegEditor
from video_agent.models import AdPlan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate-approvals", action="store_true", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--output", type=Path, required=True)
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument("--resume-first-export", action="store_true")
    recovery.add_argument("--resume-revision", action="store_true")
    args = parser.parse_args()
    resuming = args.resume_first_export or args.resume_revision
    if (
        not args.campaign.startswith("generation-verification-only-")
        or ROOT.joinpath("tmp").resolve() not in args.data_dir.resolve().parents
    ):
        raise ValueError(
            "Only a previously labelled isolated technical verification campaign under tmp/ is accepted."
        )
    if args.output.exists() and not resuming:
        raise ValueError(
            "Use a new evidence directory; known tasks remain recoverable in the campaign if interrupted."
        )
    args.output.mkdir(parents=True, exist_ok=resuming)
    original = requests.sessions.Session.request

    def local_only(session, method, url, *positional, **keywords):
        if urlparse(url).hostname not in ["127.0.0.1", "localhost", "::1"]:
            raise RuntimeError(
                "Nonlocal provider/download request blocked in scene verification"
            )
        return original(session, method, url, *positional, **keywords)

    requests.sessions.Session.request = local_only
    api = CampaignService(Settings(data_dir=args.data_dir.resolve()))
    cid = args.campaign
    report = dict(
        status="running",
        campaign_id=cid,
        approval_mode="Explicit simulated technical-test approvals; no human product-fidelity approval",
        rounds=[],
        events=[],
    )
    if resuming:
        report = read_json(args.output / "report.json")
        expected_rounds = 1 if args.resume_revision else 0
        if (
            len(report["rounds"]) != expected_rounds
            or report["status"] != "blocked_or_failed"
        ):
            raise ValueError("Recovery does not match the saved verification progress.")
        report.setdefault("recovery_history", []).append(
            dict(
                error=report.pop("error", ""),
                traceback=report.pop("traceback", ""),
                dependency=report.pop("final_dependency", None),
            )
        )
        report["status"] = "running"

    def perform(action):
        snapshot = api.snapshot(cid)
        report["events"].append(
            dict(
                action=action,
                stage=snapshot["stage"],
                version=snapshot["state"]["version"],
                signature=snapshot["signature"],
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
        state = api.snapshot(cid)
        if state["job"]["status"] != "finished":
            raise RuntimeError(state["job"]["error"])
        save_json(args.output / "report.json", report)
        return state

    try:
        snapshot = api.snapshot(cid)
        if args.resume_revision:
            if (
                snapshot["stage"] not in ["batch", "approve_final"]
                and snapshot["state"]["status"] != "complete"
            ):
                raise ValueError(
                    "The saved revision is not at a recoverable batch/export stage."
                )
            assert snapshot["state"]["version"] == report["rounds"][0]["version"] + 1
            assert snapshot["approved"], (
                "The revised plan still requires fresh approval"
            )
        elif snapshot["state"]["status"] != "complete":
            raise ValueError(
                "Finish or recover existing test work before beginning a new scene verification."
            )
        baseline = snapshot["state"]["outputs"]
        if not resuming:
            report["baseline_videos"] = [
                dict(video=o["video"], sha256=file_hash(o["video"])) for o in baseline
            ]
        spent = sum(
            json.loads(r["actual"] if r["status"] == "settled" else r["reserved"]).get(
                "generation_jobs", 0
            )
            for r in api.repository.control.usage(cid)
        )
        if (
            not args.resume_revision
            and snapshot["state"]["brief"]["limits"]["generation_jobs"] < spent + 2
        ):
            offered = api.settings.campaign_defaults(api.secrets)["limits"].get(
                "generation_jobs", 0
            )
            if offered < spent + 2:
                raise ValueError(
                    "A higher finite server generation allowance and fresh review are required for two new jobs. No limit was disabled."
                )
            api.review_server_generation_allowance(cid, snapshot["signature"])
            report["allowance_review"] = dict(
                previous=snapshot["state"]["brief"]["limits"]["generation_jobs"],
                adopted=offered,
                fresh_approval_required=True,
            )
        known = {
            t["provider_id"]
            for t in api.repository.control.tasks(cid)
            if t["kind"] == "generation"
        }
        seed = (
            report["rounds"][0]["seed"]
            if args.resume_revision
            else (
                snapshot["state"]["plan"]["ads"][2]["scenes"][0]["edit"]["seed"]
                if args.resume_first_export
                else secrets.randbelow(1_000_000_000) + 10000
            )
        )
        prior_pixels = (
            report["rounds"][0]["decoded_frame_sha256"]
            if args.resume_revision
            else None
        )
        for number, direction in enumerate(
            [
                "Slow camera drift left in neutral studio light.",
                "Slow camera drift right in warmer studio light.",
            ]
        ):
            if args.resume_revision and number == 0:
                continue
            snapshot = api.snapshot(cid)
            plan = copy.deepcopy(snapshot["state"]["plan"])
            scene = plan["ads"][2]["scenes"][0]
            resumed_export = args.resume_first_export and number == 0
            scene["edit"]["seed"] = seed + number
            scene["edit"]["generation_prompt"] = (
                scene["edit"]["generation_prompt"].split(" Revision experiment:")[0]
                + " Revision experiment: "
                + direction
            )
            key = generation_key(scene, snapshot["state"]["brief"])
            manifest = (
                api.repository.folder(cid) / "cache/generation" / key / "cache.json"
            )
            if args.resume_revision:
                assert scene == snapshot["state"]["plan"]["ads"][2]["scenes"][0], (
                    "Saved revision must match the requested direction and seed"
                )
                rendered = (
                    perform("retry") if snapshot["stage"] == "batch" else snapshot
                )
                assert (
                    rendered["stage"] == "approve_final"
                    or rendered["state"]["status"] == "complete"
                )
                final = (
                    perform("approve")
                    if rendered["stage"] == "approve_final"
                    else rendered
                )
            elif resumed_export:
                assert (
                    manifest.is_file()
                    and snapshot["state"]["version"] == report["events"][-1]["version"]
                )
                rendered = final = snapshot
            else:
                assert not manifest.exists() and not api.repository.control.task(
                    cid, "generation:" + key
                ), "Scene must be genuinely new"
                old_signature = snapshot["signature"]
                api.save_manual_plan(cid, plan, old_signature)
                changed = api.snapshot(cid)
                assert changed["stage"] == "approve_plan" and not changed["approved"]
                try:
                    api.act(cid, "generate", old_signature)
                except ValueError:
                    pass
                else:
                    raise AssertionError(
                        "Old approval unexpectedly permitted revised generation"
                    )
                perform("approve")
                perform("generate")
                rendered = perform("approve")
                final = perform("approve")
            outputs = rendered["state"]["outputs"]
            assert all(o["cache_hit"] for o in outputs[:2])
            assert [file_hash(o["video"]) for o in outputs[:2]] == [
                o["sha256"] for o in report["baseline_videos"][:2]
            ]
            generated = read_json(manifest)["result"]
            if args.resume_revision:
                assert generated["provider_id"] != report["rounds"][0]["provider_id"]
            elif not resumed_export:
                assert generated["provider_id"] not in known
            known.add(generated["provider_id"])
            decoded = subprocess.check_output(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(),
                    "-v",
                    "error",
                    "-i",
                    generated["path"],
                    "-vf",
                    "scale=64:64",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-",
                ],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            pixels = hashlib.sha256(decoded).hexdigest()
            if prior_pixels:
                assert pixels != prior_pixels, (
                    "Changed metadata is insufficient: decoded footage must change"
                )
            prior_pixels = pixels
            renderer = api.runtime(cid).renderer
            editor = FFmpegEditor(renderer, None)
            canonical_ad = AdPlan.model_validate(plan["ads"][2]).model_dump()
            unchanged_keys = [
                editor.scene_key(s, final["state"]["brief"])
                for s in canonical_ad["scenes"][1:]
            ]
            assert all(
                (
                    api.repository.folder(cid) / "cache/scene" / k / "cache.json"
                ).is_file()
                for k in unchanged_keys
            )
            if report["rounds"]:
                assert unchanged_keys == report["rounds"][0]["unaffected_scene_keys"]
            source = read_json(outputs[2]["source_manifest"])["scene_sources"]
            assert source[0]["source"] == "generated footage" and all(
                s["source"] == "image animation" for s in source[1:]
            )
            entry = dict(
                round=number + 1,
                version=final["state"]["version"],
                provider_id=generated["provider_id"],
                clip=generated["path"],
                clip_sha256=file_hash(generated["path"]),
                decoded_frame_sha256=pixels,
                prompt=scene["edit"]["generation_prompt"],
                seed=scene["edit"]["seed"],
                settings=generated["settings"],
                device=generated["execution_device"],
                elapsed_seconds=generated["elapsed_seconds"],
                conditioning_sha256=generated["conditioning_sha256"],
                workflow_sha256=generated["workflow_sha256"],
                unaffected_scene_keys=unchanged_keys,
                videos_1_and_2_reused=True,
                video=outputs[2]["video"],
                scene_sources=source,
                export=final["state"]["export_path"],
            )
            report["rounds"].append(entry)
            save_json(args.output / "report.json", report)
        report["status"] = "passed"
        report["generation_profile"] = read_json(
            api.settings.keys(api.secrets)["COMFY_PROFILE"]
        )
        report["usage"] = api.repository.control.usage(cid)
        assert all(u["status"] == "settled" for u in report["usage"])
        report["final_dependency"] = dict(
            revision_submitted=True, revision_exported=True, usage_all_settled=True
        )
        print(
            "New scene and revision exported; fresh approvals and unaffected scene/video reuse verified.",
            flush=True,
        )
    except BaseException as error:
        report.update(
            status="blocked_or_failed",
            error=f"{type(error).__name__}: {error}",
            traceback=traceback.format_exc(),
        )
        raise
    finally:
        save_json(args.output / "report.json", report)
        api.close()
        requests.sessions.Session.request = original


if __name__ == "__main__":
    main()
