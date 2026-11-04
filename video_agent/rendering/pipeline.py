"""RenderMixin: local video generation helpers."""

import json, shutil, subprocess, sys
import imageio_ffmpeg
from ..config import *
from ..storage import save_json

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

    def notebook_encoder_arguments(self, folder):
        args = [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            "1080x1920",
            "-r",
            str(VIDEO_FPS),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-threads",
            "4",
            "-pix_fmt",
            "yuv420p",
            str(folder / "silent.mp4"),
        ]
        return args

    def encode(self, ad, cues, folder):
        photos = self.identity_images()
        used = {
            key
            for scene in ad["scenes"]
            for key in scene["asset_ids"]
            if key.startswith(("commute_", "lifestyle_"))
        }
        readers = self.footage_readers(used) if used else {}
        args = self.notebook_encoder_arguments(folder)
        with (folder / "render.log").open("wb") as log:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW
                if sys.platform == "win32"
                else 0,
            )
            try:
                for number in range(20 * VIDEO_FPS):
                    if number % 90 == 0:
                        self.note(f"Encoding sample: {number // VIDEO_FPS}/20 seconds.")
                    process.stdin.write(
                        self.concept_frame(
                            ad, cues, photos, number / VIDEO_FPS, readers
                        ).tobytes()
                    )
                process.stdin.close()
                if process.wait(timeout=180):
                    raise RuntimeError("Video encoding failed; inspect render.log.")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                for reader in readers.values():
                    reader.close()

    def mux(self, folder):
        self.ffmpeg(
            [
                "-i",
                "silent.mp4",
                "-i",
                "mix.wav",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-af",
                "loudnorm=I=-16:TP=-1.5:LRA=9",
                "-t",
                "20",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "final.mp4",
            ],
            folder,
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
        video = next((s for s in probe["streams"] if s["codec_type"] == "video"))
        audio = next((s for s in probe["streams"] if s["codec_type"] == "audio"))
        assert (video["width"], video["height"]) == VIDEO_SIZE
        assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
        assert video["avg_frame_rate"] == "30/1" and int(video["nb_frames"]) == 600
        assert audio["codec_name"] == "aac" and audio["channels"] == 2
        assert abs(float(probe["format"]["duration"]) - 20) <= 0.05
        self.decode_check(path, folder)
        loudness = self.measure_loudness(path)
        assert -18 <= loudness["input_i"] <= -14, loudness
        assert loudness["input_tp"] <= -1.0, loudness
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
        result = dict(
            title=ad["title"],
            version=version,
            seconds=20,
            video=str(video),
            captions=str(captions),
            voice=str(folder / "voice.wav"),
            voice_provider="ElevenLabs"
            if self.voice_backend == "elevenlabs"
            else "Local Kokoro-82M",
            voice_id=self.voice_id,
            music=str(folder / "music-original.wav"),
            visual_source={
                "provider": "Supplied product PNGs and uploaded footage"
                if ad["video_type"] in ["student_commute", "lifestyle_story"]
                else "Supplied exact product images"
            },
            quality_check="20s, 1080x1920, H.264/AAC, full decode and audio levels verified",
            quality=quality,
            folder=str(folder),
            renderer_version=RENDER_VERSION,
            tts_runtime={"python": sys.version.split()[0], "separate_worker": False},
        )
        save_json(folder / "result.json", result)
        return result

    def build_video(self, brief, ad, version, folder):
        cues = self.voice_timeline(ad, folder)
        self.mix_audio(folder, cues, ad)
        self.caption_files(cues, folder)
        self.encode(ad, cues, folder)
        self.mux(folder)
        quality = self.validate_video(folder / "final.mp4", folder)
        save_json(
            folder / "timeline.json",
            dict(brief=brief, ad=ad, cues=cues, quality=quality),
        )
        result = self.deliver(ad, version, folder, quality)
        self.note("Video ready: " + ad["title"])
        return result
