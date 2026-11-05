"""Lazy, shared local speech resources and an explicitly selected ElevenLabs adapter."""

from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import threading
import numpy as np
import requests
import imageio_ffmpeg

VOICE_LOCK = threading.RLock()


@lru_cache(maxsize=1)
def load_kokoro(model_dir):
    import onnxruntime as ort

    # Use the supported ORT API before constructing any inference sessions.
    ort.disable_telemetry_events()
    from kokoro_onnx import Kokoro

    options = ort.SessionOptions()
    options.intra_op_num_threads, options.inter_op_num_threads = 4, 1
    session = ort.InferenceSession(
        str(Path(model_dir) / "kokoro-v1.0.int8.onnx"),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    return Kokoro.from_session(session, str(Path(model_dir) / "voices-v1.0.bin"))


class ElevenLabsVoice:
    def __init__(self, keys, model="eleven_multilingual_v2"):
        self.key = keys.get("ELEVENLABS_API_KEY", "")
        self.model = model

    def create(self, text, voice, speed=1.0, lang="en-us"):
        if not self.key or not voice:
            raise ValueError(
                "Set an ElevenLabs API key and voice ID before approving this plan."
            )
        try:
            response = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                headers={"xi-api-key": self.key},
                params={"output_format": "mp3_44100_128"},
                json={"text": text, "model_id": self.model},
                timeout=(8, 90),
            )
        except requests.RequestException:
            raise RuntimeError("ElevenLabs speech request could not connect.") from None
        if not response.ok:
            raise RuntimeError(
                f"ElevenLabs TTS: HTTP {response.status_code}. Check TTS access, voice availability and credits."
            )
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-f",
                "f32le",
                "-ac",
                "1",
                "-ar",
                "24000",
                "pipe:1",
            ],
            input=response.content,
            capture_output=True,
            timeout=30,
            creationflags=flags,
        )
        if result.returncode:
            raise RuntimeError("The ElevenLabs audio response could not be decoded.")
        samples = np.frombuffer(result.stdout, dtype="<f4").copy()
        if not len(samples) or not np.isfinite(samples).all():
            raise RuntimeError("The voice provider returned invalid audio.")
        return samples, 24000
