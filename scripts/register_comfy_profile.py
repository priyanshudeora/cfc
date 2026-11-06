"""Register settings from completed standalone probes, not adapter/mock tests.

This reads evidence and validates local model files; it performs no inference.
Use --allow-batch only after reviewing the measured time/memory and clip quality.
"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from video_agent.cache import file_hash
from video_agent.storage import save_json
from video_agent.generation import ComfyFootage
from scripts.setup_comfy import MODELS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", required=True, type=Path)
    parser.add_argument("--workflow", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-batch", action="store_true")
    parser.add_argument("--minimum-available-ram-gb", type=float)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(
            "Use a new output path; never silently change an approved profile."
        )
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in args.report]
    device = reports[0]["device"]
    workflow_hash = file_hash(args.workflow)
    for report in reports:
        if (
            report["status"] != "complete"
            or not report.get("full_video_decode")
            or report["device"] != device
            or report["workflow_sha256"] != workflow_hash
            or report["history"]["status"]["status_str"] != "success"
            or file_hash(report["clip"]) != report["clip_sha256"]
        ):
            raise ValueError(
                "Only matching completed real-probe evidence can register settings."
            )
    settings = [dict(r["settings"], strength=1) for r in reports]
    for item in settings:
        item.pop("seed", None)
    models = []
    for role, repo, revision, filename, size, sha in MODELS:
        path = args.model_root / role / filename
        if path.stat().st_size != size or file_hash(path) != sha:
            raise ValueError(
                "Model checksum differs from the selected probe model: " + filename
            )
        models.append(dict(path=str(path.resolve()), sha256=sha))
    peak_vram = max(r["peak_vram_mib"] for r in reports)
    profile = dict(
        device=device,
        device_name=reports[0]["system"]["devices"][0]["name"],
        workflow_sha256=workflow_hash,
        max_concurrency=1,
        batch_enabled=args.allow_batch,
        default_settings=settings[-1],
        tested_settings=settings,
        minimum_ram_gb=15,
        minimum_available_ram_gb=args.minimum_available_ram_gb
        if args.minimum_available_ram_gb is not None
        else (3 if device == "cpu" else 1.5),
        minimum_vram_gb=(peak_vram + 512) / 1024 if device == "cuda" else 0,
        minimum_free_vram_gb=1.5 if device == "cuda" else 0,
        benchmarks=[
            dict(report=str(p.resolve()), sha256=file_hash(p)) for p in args.report
        ],
        models=models,
        note="Sampled memory and actual elapsed time, not a performance guarantee. Low-resolution experimental output; human appearance review required.",
    )
    save_json(args.output, profile)
    adapter = ComfyFootage(
        dict(
            COMFY_DEVICE=device,
            COMFY_PROFILE=str(args.output),
            COMFY_WORKFLOW=str(args.workflow),
        ),
        None,
        "registration",
    )
    adapter.profile()
    print("Registered benchmark evidence:", args.output)
    print("Batch enabled:", args.allow_batch, "Maximum simultaneous inference jobs: 1")
    for report in reports:
        print(
            report["settings"],
            "Elapsed seconds:",
            report["elapsed_seconds"],
            "Sampled peak GPU MiB:",
            report["peak_vram_mib"],
        )


if __name__ == "__main__":
    main()
