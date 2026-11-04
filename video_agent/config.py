"""Paths and defaults shared by the application and worker threads."""

from dataclasses import dataclass
from pathlib import Path
import os
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
RENDER_VERSION = "studio-product-v2"
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

    def keys(self, overrides=None):
        values = dict(dotenv_values(ROOT / ".env"))
        values.update({k: v for k, v in os.environ.items() if k.endswith("_API_KEY")})
        values.update(overrides or {})
        return {k: str(v or "") for k, v in values.items()}

    @property
    def campaigns_dir(self):
        return self.data_dir / "campaigns"
