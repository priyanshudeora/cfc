"""Read-only readiness and stage preflight; never submit or reserve generation."""

import json
from pathlib import Path
import shutil
import subprocess

import imageio_ffmpeg
from PIL import Image

from .cache import file_hash
from .control import AllowanceExceeded
from .generation_contract import generation_key
from .storage import read_json
from .usage import charge
from .voice_setup import TTS_SETUP, tts_python, validate_voice_models


def editing_readiness():
    try:
        programs = [imageio_ffmpeg.get_ffmpeg_exe(), shutil.which("ffprobe")]
        if not all(programs):
            raise ValueError(
                "FFprobe is missing. Install FFmpeg with FFprobe and add its bin directory to PATH."
            )
        for program in programs:
            subprocess.run(
                [program, "-version"],
                capture_output=True,
                check=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        return dict(
            available=True,
            message="FFmpeg and FFprobe are executable. Editing does not require ComfyUI.",
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return dict(
            available=False,
            message=f"Editing unavailable: {error}. Install FFmpeg/FFprobe; retry the readiness check.",
        )


def voice_readiness(model_dir, keys):
    try:
        python = tts_python(keys)
        if not python.is_file():
            raise ValueError(f"Speech Python is missing: {python}")
        validate_voice_models(model_dir)
        return dict(
            available=None,
            message="Runtime path and model SHA-256 checks pass. Run Check local setup to initialize and test actual voice inference.",
            python=str(python),
        )
    except (ValueError, OSError) as error:
        return dict(available=False, message=f"{error}. Next: {TTS_SETUP}")


def validate_assets(brief, assets, footage, scenes):
    used = {asset for scene in scenes for asset in scene["asset_ids"]}
    used.update(
        scene["edit"].get("conditioning_asset")
        for scene in scenes
        if scene["edit"]["mode"] != "reused"
    )
    for asset in used:
        metadata = assets.get(asset, {})
        path = Path(footage.get(asset) or metadata.get("path") or "__missing__")
        expected = brief.get("asset_hashes", {}).get(asset)
        if not path.is_file() or not expected or file_hash(path) != expected:
            raise ValueError(
                f"Product/footage asset is missing or changed: {asset}. Restore or upload it and review the brief again."
            )
        if asset not in footage:
            with Image.open(path) as image:
                image.verify()


def generation_preflight(adapter, scenes, cache, brief, stage):
    generated = [s for s in scenes if s["edit"]["mode"] != "reused"]
    if not generated:
        return
    if brief.get("generation") != adapter.scope():
        raise ValueError(
            "Generation profile, workflow or device changed. Save and approve a fresh plan and estimate."
        )
    if not brief["generation"].get("batch_enabled") and (
        len(generated) > 1 or stage == "batch"
    ):
        raise ValueError(
            "This CPU/experimental profile supports one sample scene only; campaign batches are disabled. Benchmark a batch-capable profile and approve a fresh plan before continuing."
        )
    new, provider_needed = set(), False
    for scene in generated:
        key = generation_key(scene, brief)
        manifest = cache.folder / "generation" / key / "cache.json"
        if manifest.is_file():
            saved = read_json(manifest)
            if all(
                Path(p).is_file() and file_hash(p) == sha
                for p, sha in saved["hashes"].items()
            ):
                adapter.verify_clip(
                    saved["result"]["path"], saved["result"]["settings"]
                )
                continue
        provider_needed = True
        task = cache.control.task(cache.cid, "generation:" + key)
        if (
            not task
            or not task.get("provider_id")
            or task["status"] in ["failed", "cancelled", "paused"]
        ):
            new.add(key)
    if provider_needed:
        ready = adapter.readiness()
        if not ready["available"]:
            raise ValueError(
                "AI footage unavailable: "
                + ready["message"]
                + ". Follow docs/local-generation.md to start the measured server/profile. Editing remains a separate explicit choice."
            )
    # Informational preflight only; atomic reservations remain authoritative at submission.
    totals = {}
    for row in cache.control.usage(cache.cid):
        for name, value in json.loads(
            row["actual"] if row["status"] == "settled" else row["reserved"]
        ).items():
            totals[name] = totals.get(name, 0) + value
    for name, value in charge(dict(generation_jobs=len(new)), cache.rates).items():
        if totals.get(name, 0) + value > cache.limits.get(name, 0):
            raise AllowanceExceeded(
                f"Approved allowance exhausted: {name}. {len(new)} new footage jobs need an updated estimate and approval; no footage submitted."
            )
