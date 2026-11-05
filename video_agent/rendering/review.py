"""Rendered-media contract checks and a contact sheet; semantic review remains human."""

from PIL import Image, ImageDraw
from .editor import run_ffmpeg
from ..cache import file_hash
from ..storage import save_json, digest


def review_render(renderer, brief, ad, cues, folder):
    if len(cues) != len(ad["scenes"]):
        raise ValueError("Rendered narration/caption timeline is incomplete.")
    elapsed = 0
    for scene, cue in zip(ad["scenes"], cues):
        if (
            cue["caption"] != scene["caption"]
            or not elapsed <= cue["start"] < cue["end"] <= elapsed + scene["seconds"]
        ):
            raise ValueError(
                "Rendered narration/captions do not correspond to the approved scene timing."
            )
        elapsed += scene["seconds"]
        if not scene["asset_ids"]:
            raise ValueError("Rendered scene lacks its approved visual source.")
    if elapsed != 20:
        raise ValueError("Rendered timeline must total exactly twenty seconds.")
    for key, expected in brief.get("asset_hashes", {}).items():
        if (
            key in renderer.commute_files
            and file_hash(renderer.commute_files[key]) != expected
        ):
            raise ValueError("Planned footage changed while rendering: " + key)
        if (
            key in renderer.assets
            and file_hash(renderer.assets[key]["path"]) != expected
        ):
            raise ValueError("Product/reference asset changed while rendering: " + key)
    manifest = __import__("json").loads(
        (folder / "edit_manifest.json").read_text(encoding="utf-8")
    )
    if manifest["timeline"] != ad:
        raise ValueError(
            "The rendered edit manifest differs from the approved executable timeline."
        )
    if " ".join(event["caption"] for event in manifest["caption_events"]) != " ".join(
        scene["caption"] for scene in ad["scenes"]
    ):
        raise ValueError(
            "Burned caption events do not exactly transcribe the approved copy."
        )
    frames = []
    elapsed = 0
    for i, scene in enumerate(ad["scenes"]):
        path = folder / f"review-{i}.jpg"
        run_ffmpeg(
            [
                "-ss",
                cues[i]["start"] + min(0.35, (cues[i]["end"] - cues[i]["start"]) / 2),
                "-i",
                folder / "final.mp4",
                "-frames:v",
                1,
                "-vf",
                "scale=216:384",
                path,
            ],
            folder,
        )
        with Image.open(path) as image:
            frames.append(image.convert("RGB").copy())
        elapsed += scene["seconds"]
    sheet = Image.new("RGB", (216 * len(frames), 414), "white")
    draw = ImageDraw.Draw(sheet)
    for i, image in enumerate(frames):
        sheet.paste(image, (216 * i, 30))
        draw.text((216 * i + 8, 8), f"Scene {i + 1}", fill="black")
    sheet.save(folder / "review-contact-sheet.jpg")
    result = dict(
        status="contract_passed",
        timeline_hash=digest(ad),
        actual_video_sha256=file_hash(folder / "final.mp4"),
        checks=[
            "Exact executable timeline",
            "Source-file integrity",
            "Narration and caption timing",
            "Full audio/video decode and technical checks run separately",
        ],
        human_review_required=[
            "Exact product appearance in the actual video",
            "Readability on a phone",
            "Pronunciation, tone and music balance",
        ],
        advertising_performance="Not evaluated; requires campaign results",
    )
    save_json(folder / "media_review.json", result)
    return result
