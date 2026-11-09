"""Lazy, shared local speech resources and an explicitly selected ElevenLabs adapter."""

from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import threading
import numpy as np
import requests
import imageio_ffmpeg
import atexit
import json
import queue
import tempfile
import os
from ..config import ROOT
from ..voice_setup import tts_python, TTS_SETUP, validate_voice_models

VOICE_LOCK = threading.RLock()


class IsolatedKokoro:
    """One persistent CPU worker in the supported, separate TTS environment."""

    def __init__(self, model_dir, python):
        self.lock, self.responses = threading.RLock(), queue.Queue()
        self.process = subprocess.Popen(
            [str(python), "-u", str(ROOT / "scripts/tts_worker.py"), str(model_dir)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=dict(os.environ, ORT_DISABLE_TELEMETRY="1"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        def read():
            for line in self.process.stdout:
                self.responses.put(line)
            self.responses.put(None)

        threading.Thread(target=read, daemon=True, name="local-voice-protocol").start()
        try:
            self.info = self.receive()
        except BaseException:
            self.close()
            raise
        atexit.register(self.close)

    def receive(self):
        try:
            line = self.responses.get(timeout=180)
            if line is None:
                raise RuntimeError("Local voice worker exited unexpectedly")
            response = json.loads(line)
            if not response.get("ok"):
                raise RuntimeError(
                    response.get("error", "Local voice initialization failed")
                )
            return response
        except (queue.Empty, ValueError, RuntimeError) as error:
            self.close()
            raise RuntimeError(
                f"Local voice unavailable: {error}. Run: {TTS_SETUP}"
            ) from None

    def create(self, text, voice, speed=1.0, lang="en-us"):
        with self.lock, tempfile.TemporaryDirectory(prefix="cfc-speech-") as folder:
            if self.process.poll() is not None:
                raise RuntimeError(
                    "Local voice worker stopped; retry the stage to restart it."
                )
            if voice not in self.info["voices"]:
                raise ValueError("Selected local voice ID is unavailable: " + voice)
            output = Path(folder) / "speech.npy"
            self.process.stdin.write(
                json.dumps(
                    dict(
                        text=text,
                        voice=voice,
                        speed=speed,
                        lang=lang,
                        output=str(output),
                    )
                )
                + "\n"
            )
            self.process.stdin.flush()
            response = self.receive()
            samples = np.load(output, allow_pickle=False)
            if (
                not len(samples)
                or not np.isfinite(samples).all()
                or response["rate"] != 24000
            ):
                raise RuntimeError("Local speech worker returned invalid audio")
            return samples, response["rate"]

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('{"operation":"close"}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                # This is our own speech child, never a shared ComfyUI process.
                self.process.kill()
                self.process.wait(timeout=10)


@lru_cache(maxsize=2)
def _isolated_kokoro(model_dir, python, identity):
    return IsolatedKokoro(model_dir, python)


def local_voice(model_dir, keys=None):
    from ..storage import digest
    from ..voice_setup import MODEL_SHA256

    python = tts_python(keys)
    if not python.is_file():
        raise ValueError(
            f"Local speech environment missing: {python}. Run: {TTS_SETUP}"
        )
    validate_voice_models(model_dir)
    # Changes to a worker/runtime invalidate its cached session.
    identity = digest(
        [
            MODEL_SHA256,
            python.stat().st_mtime_ns,
            (ROOT / "requirements-tts.txt").read_text(),
            (ROOT / "scripts/tts_worker.py").stat().st_mtime_ns,
        ]
    )
    engine = _isolated_kokoro(str(model_dir), str(python), identity)
    if engine.process.poll() is not None:
        _isolated_kokoro.cache_clear()
        engine = _isolated_kokoro(str(model_dir), str(python), identity)
    return engine


@lru_cache(maxsize=1)
def load_kokoro(model_dir):
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
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
