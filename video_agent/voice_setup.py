"""Explicit, checksum-verified local speech setup. Inference never downloads models."""

from functools import lru_cache
from pathlib import Path
import os
import requests

from .cache import file_hash
from .config import MODEL_BASE, MODEL_FILES, ROOT

MODEL_SHA256 = {
    "kokoro-v1.0.int8.onnx": "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
TTS_SETUP = "python scripts\\setup_tts.py --download-models"


def tts_python(keys=None):
    return Path(
        (keys or {}).get("CFC_TTS_PYTHON")
        or os.environ.get("CFC_TTS_PYTHON")
        or ROOT
        / ".venv-kokoro"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )


@lru_cache(maxsize=16)
def checked_hash(path, size, modified):
    return file_hash(path)


def validate_voice_models(folder):
    for name, size in MODEL_FILES.items():
        path = Path(folder) / name
        if not path.is_file():
            raise ValueError(f"Local voice model missing: {name}. Run: {TTS_SETUP}")
        stat = path.stat()
        if (
            stat.st_size != size
            or checked_hash(str(path), stat.st_size, stat.st_mtime_ns)
            != MODEL_SHA256[name]
        ):
            raise ValueError(
                f"Local voice model failed SHA-256 verification: {name}. Run: {TTS_SETUP}"
            )


def download_voice_models(folder, note=print):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, size in MODEL_FILES.items():
        path = folder / name
        if (
            path.is_file()
            and path.stat().st_size == size
            and file_hash(path) == MODEL_SHA256[name]
        ):
            note("Verified cached voice model: " + name)
            continue
        partial = folder / (name + ".part")
        note("Downloading voice model: " + name)
        try:
            with requests.get(
                MODEL_BASE + name, stream=True, timeout=(10, 60)
            ) as response:
                response.raise_for_status()
                with partial.open("wb") as output:
                    total, progress = 0, -1
                    for chunk in response.iter_content(1024 * 1024):
                        total += len(chunk)
                        if total > size:
                            raise ValueError(
                                "Voice model response exceeds expected size"
                            )
                        output.write(chunk)
                        percent = int(total * 100 / size)
                        if percent // 10 > progress:
                            note(f"{name}: {percent}%")
                            progress = percent // 10
            if (
                partial.stat().st_size != size
                or file_hash(partial) != MODEL_SHA256[name]
            ):
                raise ValueError(
                    "Downloaded voice model failed SHA-256 verification: " + name
                )
            partial.replace(path)
            note("SHA-256 verified: " + name)
        except requests.RequestException as error:
            raise RuntimeError(
                f"Voice download failed ({type(error).__name__}). Retry {TTS_SETUP}; no generation was submitted."
            ) from None
    validate_voice_models(folder)
