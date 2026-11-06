"""Submit ONE real local ComfyUI probe and record measurements, never mocks.

Run with the app environment. ComfyUI runs separately. An unfinished report keeps
the provider ID: --resume polls that ID instead of generating another clip.
"""

import argparse
import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import psutil
import requests
import numpy as np
import imageio_ffmpeg


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8188")
    parser.add_argument("--workflow", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--device", choices=["cpu", "cuda"], required=True)
    parser.add_argument("--pid", type=int, required=True, help="ComfyUI inference PID")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--width", type=int, default=192)
    parser.add_argument("--height", type=int, default=320)
    parser.add_argument("--frames", type=int, default=9)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=23456)
    parser.add_argument(
        "--prompt",
        default=(
            "A realistic product shot of the exact bottle in the supplied image. "
            "The camera slowly moves closer to the bottle on the simple studio surface. "
            "Soft natural studio light moves across the bottle, retaining its silhouette, "
            "colour, cap and proportions. A quiet restrained motion. No people, no text, "
            "no packaging, no additional products. Keep the object recognizable."
        ),
    )
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if (
        not 64 <= args.width <= 512
        or args.width % 32
        or not 64 <= args.height <= 512
        or args.height % 32
        or not 9 <= args.frames <= 49
        or (args.frames - 1) % 8
        or not 1 <= args.fps <= 30
        or not 1 <= args.steps <= 30
    ):
        parser.error(
            "Use conservative LTX dimensions, 8n+1 frames and bounded steps/fps"
        )
    if urlparse(args.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Only a local free server is supported")
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / "report.json"

    def get(route):
        response = requests.get(args.url.rstrip("/") + route, timeout=(5, 30))
        response.raise_for_status()
        return response.json()

    stats = get("/system_stats")
    actual_device = stats["devices"][0]["type"]
    if actual_device != args.device:
        raise ValueError(f"Requested {args.device}, server reports {actual_device}")
    if args.device == "cpu" and "--cpu" not in stats["system"].get("argv", []):
        raise ValueError("CPU-only verification requires a server started with --cpu")
    if report_path.exists():
        if not args.resume:
            raise ValueError("Report exists. Use --resume; never blindly resubmit.")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if (
            report["workflow_sha256"]
            != hashlib.sha256(args.workflow.read_bytes()).hexdigest()
        ):
            raise ValueError(
                "Resume requires the original workflow; the saved job is unchanged"
            )
        if (
            report["image_sha256"]
            != hashlib.sha256(args.image.read_bytes()).hexdigest()
        ):
            raise ValueError("Resume requires the original conditioning image")
        if not report.get("provider_id"):
            raise ValueError(
                "Unknown submission: inspect server history before retrying"
            )
    else:
        if args.resume:
            raise ValueError("No saved submission to resume")
        workflow = json.loads(args.workflow.read_text(encoding="utf-8"))
        marker = "cfc-standalone-" + uuid.uuid4().hex
        with args.image.open("rb") as source:
            response = requests.post(
                args.url + "/upload/image",
                files={"image": (marker + args.image.suffix, source)},
                data={"type": "input", "overwrite": "false"},
                timeout=(5, 30),
            )
        response.raise_for_status()
        uploaded = response.json()
        image = "/".join(filter(None, [uploaded.get("subfolder"), uploaded["name"]]))
        replacements = {
            "__PROMPT__": args.prompt,
            "__SEED__": args.seed,
            "__IMAGE__": image,
            "__WIDTH__": args.width,
            "__HEIGHT__": args.height,
            "__FRAMES__": args.frames,
            "__FPS__": args.fps,
            "__STEPS__": args.steps,
        }

        def bind(value):
            if isinstance(value, str):
                return replacements.get(value, value)
            if isinstance(value, list):
                return [bind(item) for item in value]
            if isinstance(value, dict):
                return {k: bind(v) for k, v in value.items()}
            return value

        graph = bind(workflow)
        report = dict(
            status="submitting",
            marker=marker,
            started_epoch=time.time(),
            device=args.device,
            system=stats,
            workflow=graph,
            settings=dict(
                width=args.width,
                height=args.height,
                frames=args.frames,
                fps=args.fps,
                steps=args.steps,
                seed=args.seed,
            ),
            workflow_sha256=hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            image_sha256=hashlib.sha256(args.image.read_bytes()).hexdigest(),
            conditioning="LoadImage -> LTXVImgToVideo.image -> SamplerCustom.latent_image",
            peak_rss_bytes=0,
            peak_vram_mib=0,
            vram_measurement="Whole-device used VRAM sampled by nvidia-smi, including OS/other processes",
            memory_measurement="ComfyUI process RSS sampled while polling; not allocated tensor memory",
        )
        save(report_path, report)
        response = requests.post(
            args.url + "/prompt",
            json={
                "prompt": graph,
                "client_id": marker,
                "extra_data": {"cfc_probe": marker},
            },
            timeout=(5, 30),
        )
        report["submission_response"] = response.json()
        if response.status_code >= 400:
            report["status"] = "rejected"
        save(report_path, report)
        response.raise_for_status()
        report["provider_id"] = response.json()["prompt_id"]
        report["status"] = "submitted"
        save(report_path, report)
    process = psutil.Process(args.pid)
    started = time.monotonic()
    while time.monotonic() - started < args.timeout:
        report["elapsed_seconds"] = round(time.time() - report["started_epoch"], 2)
        try:
            report["peak_rss_bytes"] = max(
                report["peak_rss_bytes"], process.memory_info().rss
            )
            report["peak_private_bytes"] = max(
                report.get("peak_private_bytes", 0),
                getattr(process.memory_info(), "private", process.memory_info().rss),
            )
            report["minimum_available_ram_bytes"] = min(
                report.get(
                    "minimum_available_ram_bytes", psutil.virtual_memory().available
                ),
                psutil.virtual_memory().available,
            )
            sample = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if sample.returncode == 0:
                report["peak_vram_mib"] = max(
                    report["peak_vram_mib"], int(sample.stdout.splitlines()[0])
                )
        except (psutil.Error, OSError, ValueError, subprocess.TimeoutExpired):
            pass
        history = get("/history/" + report["provider_id"]).get(
            report["provider_id"], {}
        )
        save(report_path, report)
        if history.get("status", {}).get("status_str") == "error":
            report.update(status="failed", history=history)
            save(report_path, report)
            raise RuntimeError("ComfyUI failed. See recorded execution error.")
        for node in history.get("outputs", {}).values():
            for item in (
                node.get("images", []) + node.get("videos", []) + node.get("gifs", [])
            ):
                suffix = Path(item.get("filename", "")).suffix
                if suffix not in {".webm", ".mp4", ".mov"}:
                    continue
                response = requests.get(
                    args.url + "/view", params=item, timeout=(5, 60)
                )
                response.raise_for_status()
                clip = args.output / ("generated" + suffix)
                clip.write_bytes(response.content)
                probe = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_streams",
                        "-count_frames",
                        "-show_format",
                        "-of",
                        "json",
                        str(clip),
                    ],
                    capture_output=True,
                    check=True,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                decoded = json.loads(probe.stdout)
                video = next(
                    s for s in decoded["streams"] if s["codec_type"] == "video"
                )
                if int(video.get("nb_read_frames", 0)) < 2:
                    raise ValueError("A still image does not verify generated video")
                if (video["width"], video["height"], int(video["nb_read_frames"])) != (
                    report["settings"]["width"],
                    report["settings"]["height"],
                    report["settings"]["frames"],
                ):
                    raise ValueError(
                        "Generated dimensions/frame count differ from requested settings"
                    )
                pixels = subprocess.run(
                    [
                        imageio_ffmpeg.get_ffmpeg_exe(),
                        "-v",
                        "error",
                        "-i",
                        str(clip),
                        "-vf",
                        "scale=64:64",
                        "-f",
                        "rawvideo",
                        "-pix_fmt",
                        "rgb24",
                        "-",
                    ],
                    capture_output=True,
                    check=True,
                    timeout=60,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                frames = (
                    np.frombuffer(pixels.stdout, dtype=np.uint8)
                    .reshape(-1, 64, 64, 3)
                    .astype(np.float32)
                )
                report["frame_standard_deviations"] = frames.std(
                    axis=(1, 2, 3)
                ).tolist()
                report["mean_adjacent_frame_difference"] = float(
                    np.abs(np.diff(frames, axis=0)).mean()
                )
                if min(report["frame_standard_deviations"]) < 1:
                    raise ValueError(
                        "Generated clip contains blank frames; inspect the actual inference result"
                    )
                report["full_video_decode"] = True
                report.update(
                    status="complete",
                    clip=str(clip.resolve()),
                    output=item,
                    probe=decoded,
                    history=history,
                    clip_sha256=hashlib.sha256(clip.read_bytes()).hexdigest(),
                )
                save(report_path, report)
                print(
                    json.dumps(
                        {
                            k: report[k]
                            for k in [
                                "status",
                                "clip",
                                "elapsed_seconds",
                                "peak_rss_bytes",
                                "peak_vram_mib",
                            ]
                        }
                    )
                )
                return
        time.sleep(2)
    report["status"] = "still_running_or_queued"
    save(report_path, report)
    raise TimeoutError(
        "Probe wait expired. Known job retained; --resume does not resubmit."
    )


if __name__ == "__main__":
    main()
