"""RenderMixin: local video generation helpers."""

import json, shutil, subprocess, sys
import imageio_ffmpeg
from ..config import *
from ..storage import save_json, read_json

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


class RenderMixin:
    def ffmpeg(self, arguments, folder):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *arguments],
            cwd=folder,
            capture_output=True,
            timeout=120,
            creationflags=flags,
        )
        if result.returncode:
            raise RuntimeError(
                "FFmpeg failed: " + result.stderr.decode(errors="replace")[-350:]
            )

    def probe(self, path):
        program = shutil.which("ffprobe")
        if not program:
            raise RuntimeError("FFprobe is required to verify the sample.")
        result = subprocess.run(
            [
                program,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode:
            raise RuntimeError("FFprobe could not read the sample.")
        return json.loads(result.stdout)

    def measure_loudness(self, path):
        result = subprocess.run(
            [
                FFMPEG,
                "-hide_banner",
                "-i",
                str(path),
                "-vn",
                "-af",
                "loudnorm=I=-16:TP=-1.5:LRA=9:print_format=json",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        message = result.stderr.decode(errors="replace")
        if result.returncode or "{" not in message:
            raise RuntimeError("Loudness measurement failed.")
        data = json.loads(message[message.rfind("{") : message.rfind("}") + 1])
        return {key: float(data[key]) for key in ["input_i", "input_tp", "input_lra"]}

    def decode_check(self, path, folder):
        self.ffmpeg(
            ["-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
            folder,
        )

    def validate_video(self, path, folder):
        probe = self.probe(path)
        video = next((s for s in probe["streams"] if s["codec_type"] == "video"), None)
        audio = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
        if not video or not audio:
            raise ValueError(
                "The export must contain both readable video and audio streams."
            )
        checks = [
            (
                (video["width"], video["height"]) == VIDEO_SIZE,
                "Incorrect export dimensions",
            ),
            (
                video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p",
                "Incorrect video format",
            ),
            (
                video["avg_frame_rate"] == "30/1" and int(video["nb_frames"]) == 600,
                "Incorrect frame count or frame rate",
            ),
            (
                audio["codec_name"] == "aac" and audio["channels"] == 2,
                "Incorrect audio format",
            ),
            (
                abs(float(probe["format"]["duration"]) - 20) <= 0.05,
                "Incorrect video duration",
            ),
        ]
        for valid, message in checks:
            if not valid:
                raise ValueError(message)
        self.decode_check(path, folder)
        loudness = self.measure_loudness(path)
        if not -18 <= loudness["input_i"] <= -14 or loudness["input_tp"] > -1:
            raise ValueError(
                "Export audio levels are outside the accepted range: " + str(loudness)
            )
        result = dict(
            status="passed",
            duration=20,
            width=1080,
            height=1920,
            fps=30,
            frames=600,
            video="H.264",
            audio="AAC stereo 48 kHz",
            loudness=loudness,
            full_audio_video_decode=True,
        )
        save_json(folder / "quality_check.json", result)
        return result

    def deliver(self, ad, version, folder, quality):
        destination = self.folder / "deliverables" / f"v{version:03d}"
        destination.mkdir(parents=True, exist_ok=True)
        stem = {
            "product_demo": "01_product_demo_sample",
            "student_commute": "02_student_commute",
            "lifestyle_story": "02_lifestyle_story",
            "product_reveal": "03_product_reveal",
        }[ad["video_type"]]
        video, captions = (destination / (stem + ".mp4"), destination / (stem + ".srt"))
        shutil.copy2(folder / "final.mp4", video)
        shutil.copy2(folder / "captions.srt", captions)
        from ..cache import file_hash

        result = dict(
            title=ad["title"],
            version=version,
            seconds=20,
            video=str(video),
            video_sha256=file_hash(video),
            captions=str(captions),
            captions_sha256=file_hash(captions),
            voice=str(folder / "voice.wav"),
            voice_provider="ElevenLabs"
            if self.voice_backend == "elevenlabs"
            else "Local Kokoro-82M",
            voice_id=self.voice_id,
            music=str(folder / "music-original.wav"),
            visual_source={
                "scenes": read_json(folder / "edit_manifest.json").get(
                    "scene_sources", []
                )
            },
            source_manifest=str(folder / "edit_manifest.json"),
            quality_check="20s, 1080x1920, H.264/AAC, full decode and audio levels verified",
            quality=quality,
            folder=str(folder),
            renderer_version=RENDER_VERSION,
            tts_runtime={"python": sys.version.split()[0], "separate_worker": False},
        )
        save_json(folder / "result.json", result)
        return result

    def build_video(self, brief, ad, version, folder):
        from .editor import FFmpegEditor

        cues = self.voice_timeline(ad, folder)
        self.make_music(folder, ad)
        editor = FFmpegEditor(self, self.media_cache, workers=self.settings.cpu_workers)
        editor.encode(ad, brief, folder)
        editor.mux(ad, cues, folder)
        from .review import review_render

        media_review = review_render(self, brief, ad, cues, folder)
        quality = self.validate_video(folder / "final.mp4", folder)
        quality["media_contract"] = media_review
        save_json(
            folder / "timeline.json",
            dict(brief=brief, ad=ad, cues=cues, quality=quality),
        )
        result = dict(
            folder=str(folder),
            quality=quality,
            _files=[
                folder / name
                for name in [
                    "final.mp4",
                    "voice.wav",
                    "captions.srt",
                    "edit_manifest.json",
                    "media_review.json",
                    "review-contact-sheet.jpg",
                ]
            ],
        )
        self.note("Video ready: " + ad["title"])
        return result
