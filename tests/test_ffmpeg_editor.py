"""Self-contained FFmpeg integration; synthetic assets/audio, no provider credits."""

from pathlib import Path
import shutil
import numpy as np
import pytest
from PIL import Image, ImageDraw
from video_agent.cache import MediaCache, file_hash
from video_agent.control import DEFAULT_LIMITS
from video_agent.rendering.editor import FFmpegEditor, run_ffmpeg
from video_agent.timeline import revise_timeline
from test_workflow import studio as studio, make_campaign


@pytest.mark.skipif(
    not shutil.which("ffprobe"),
    reason="Install FFprobe to run the local FFmpeg smoke test",
)
def test_ffmpeg_export_edits_and_unchanged_scene_reuse(studio, tmp_path, monkeypatch):
    cid = make_campaign(studio)
    renderer = studio.runtime(cid).renderer
    state = studio.snapshot(cid)["state"]
    renderer.brief = state["brief"]
    image_path = Path(renderer.assets["product_main"]["path"])
    image = Image.open(image_path).convert("RGB")
    ImageDraw.Draw(image).rectangle((20, 50, 170, 350), fill="yellow")
    image.save(image_path)
    # These test images are intentionally edited before creating an approved render input.
    renderer.assets["product_main"]["sha256"] = file_hash(image_path)
    renderer.media_cache = MediaCache(
        tmp_path / "cache",
        studio.repository.control,
        cid,
        dict(DEFAULT_LIMITS, render_seconds=500),
    )
    folder = tmp_path / "export"
    folder.mkdir()
    calls = []

    class Voice:
        def create(self, text, **kwargs):
            calls.append(text)
            t = np.arange(24000) / 24000
            return (
                0.1
                * np.sin(2 * np.pi * 220 * t)
                * np.minimum(t / 0.02, 1)
                * np.minimum((1 - t) / 0.02, 1)
            ).astype(np.float32), 24000

    monkeypatch.setattr(renderer, "notebook_kokoro", lambda: Voice())
    ad = state["plan"]["ads"][0]
    cues = renderer.voice_timeline(ad, folder)
    first_calls = len(calls)
    renderer.generate_voice(ad, folder)
    assert len(calls) == first_calls
    renderer.make_music(folder, ad)
    editor = FFmpegEditor(
        renderer, renderer.media_cache, size=(240, 426), fps=10, workers=2
    )
    first = editor.encode(ad, state["brief"], folder)
    editor.mux(ad, cues, folder)
    info = renderer.probe(folder / "final.mp4")
    assert abs(float(info["format"]["duration"]) - 20) < 0.1
    assert any(item["codec_name"] == "aac" for item in info["streams"])
    assert any(
        item.get("width") == 240 and item.get("height") == 426
        for item in info["streams"]
    )
    renderer.decode_check(folder / "final.mp4", folder)
    changed = revise_timeline(
        state["plan"], 0, "scene 2 move product left; quieter music; fade transitions"
    )["ads"][0]
    second = editor.encode(changed, state["brief"], folder)
    assert first[1]["path"] != second[1]["path"]
    assert all(first[i]["path"] == second[i]["path"] for i in [0, 2, 3, 4])
    assert changed["audio"]["music_gain"] < ad["audio"]["music_gain"]
    for name, result in [("before", first[1]), ("after", second[1])]:
        run_ffmpeg(
            ["-ss", 1, "-i", result["path"], "-frames:v", 1, folder / (name + ".png")],
            folder,
        )
    assert file_hash(folder / "before.png") != file_hash(folder / "after.png")
    assert (folder / "captions.ass").is_file() and (
        folder / "edit_manifest.json"
    ).is_file()


@pytest.mark.skipif(
    not shutil.which("ffprobe"),
    reason="FFprobe required for local editing smoke checks",
)
def test_clip_boundaries_and_mask_filters_execute(studio, tmp_path):
    cid = make_campaign(studio)
    renderer = studio.runtime(cid).renderer
    state = studio.snapshot(cid)["state"]
    renderer.brief = state["brief"]
    source = tmp_path / "clip.mp4"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=240x426:r=10:d=2",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-pix_fmt",
            "yuv420p",
            source,
        ],
        tmp_path,
    )
    renderer.commute_files["lifestyle_clip"] = str(source)
    cache = MediaCache(
        tmp_path / "cache", studio.repository.control, cid, DEFAULT_LIMITS
    )
    editor = FFmpegEditor(renderer, cache, size=(240, 426), fps=10, workers=1)
    import copy

    scene = copy.deepcopy(state["plan"]["ads"][1]["scenes"][0])
    scene["asset_ids"] = ["lifestyle_clip"]
    scene["edit"].update(trim_start=0.2, trim_end=0.8, mask="circle")
    clip = editor.scene(scene, 0, state["brief"])
    assert (
        abs(
            float(renderer.probe(clip["path"])["format"]["duration"]) - scene["seconds"]
        )
        < 0.15
    )
    scene["asset_ids"] = ["product_main"]
    scene["edit"].update(mask="wipe_left", trim_start=0, trim_end=None)
    still = editor.scene(scene, 0, state["brief"])
    assert Path(still["path"]).is_file() and clip["path"] != still["path"]
