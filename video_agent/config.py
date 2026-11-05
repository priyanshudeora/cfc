"""Paths and defaults shared by the application and worker threads."""

from dataclasses import dataclass
from pathlib import Path
import os
import json
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
LEGACY_ID = "boat-airdopes-141-black-20261104"
LEGACY_DIR = ROOT / "data" / "video-demo" / LEGACY_ID
REFERENCE_DIR = ROOT / "assets" / "airdopes_141"
MODEL_CACHE = ROOT / "data" / "models" / "kokoro"
if (
    not MODEL_CACHE.exists()
    and (ROOT / "data" / "video-demo" / "models" / "kokoro").exists()
):
    MODEL_CACHE = ROOT / "data" / "video-demo" / "models" / "kokoro"
VIDEO_SIZE, VIDEO_FPS, AUDIO_SR = (1080, 1920), 30, 24000
RENDER_VERSION = "studio-ffmpeg-v3"
CYAN, INK, MUTED = "#00AABD", "#10272E", "#526A73"
MODEL_FILES = {"kokoro-v1.0.int8.onnx": 92361271, "voices-v1.0.bin": 28214398}
MODEL_BASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.environ.get("CFC_DATA_DIR", ROOT / "data" / "studio"))
    model_dir: Path = MODEL_CACHE
    reference_dir: Path = REFERENCE_DIR
    cpu_workers: int = max(1, min(4, int(os.environ.get("CFC_CPU_WORKERS", "2"))))
    gpu_workers: int = max(1, min(4, int(os.environ.get("CFC_GPU_WORKERS", "1"))))
    provider_workers: int = max(
        1, min(4, int(os.environ.get("CFC_PROVIDER_WORKERS", "1")))
    )

    def keys(self, overrides=None):
        values = dict(dotenv_values(ROOT / ".env"))
        values.update(
            {
                k: v
                for k, v in os.environ.items()
                if k.endswith("_API_KEY")
                or k.startswith(("COMFY_", "WHISPER_", "ELEVENLABS_", "CFC_"))
            }
        )
        values.update(overrides or {})
        return {k: str(v or "") for k, v in values.items()}

    def campaign_defaults(self, overrides=None):
        """Server policy for new campaigns; existing saved policy is immutable on form edits."""
        keys = self.keys(overrides)
        defaults = dict(
            model=keys.get("CFC_PLANNING_MODEL") or "gemini-2.5-flash",
            limits=json.loads(keys.get("CFC_DEFAULT_LIMITS_JSON") or "{}"),
            usage_rates=json.loads(keys.get("CFC_DEFAULT_USAGE_RATES_JSON") or "{}"),
        )
        if not all(isinstance(defaults[k], dict) for k in ["limits", "usage_rates"]):
            raise ValueError(
                "Server campaign defaults must use JSON objects for limits and rates."
            )
        return defaults

    @property
    def campaigns_dir(self):
        return self.data_dir / "campaigns"
