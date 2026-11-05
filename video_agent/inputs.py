"""Hash-cached local input analysis; optional whisper.cpp transcription."""

from pathlib import Path
import subprocess
import shutil
from PIL import Image
from .cache import file_hash
from .storage import read_json, save_json, digest
from .rendering.editor import run_ffmpeg
from .config import ROOT


def transcription_config(keys):
    """Explicit settings take precedence over this machine's optional local install."""
    manifest = ROOT / "data" / "tools" / "whisper" / "install.json"
    installed = read_json(manifest) if manifest.is_file() else {}
    binary = (
        keys.get("WHISPER_CPP_BIN")
        or shutil.which("whisper-cli")
        or installed.get("binary")
    )
    model = keys.get("WHISPER_CPP_MODEL") or installed.get("model")
    return binary, model


class InputAnalyzer:
    def __init__(self, folder, keys):
        self.folder, self.keys = Path(folder).resolve(), keys

    def transcribe(self, source, folder):
        source, folder = Path(source).resolve(), Path(folder).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        binary, model = transcription_config(self.keys)
        if (
            not binary
            or not model
            or not Path(binary).is_file()
            or not Path(model).is_file()
        ):
            return dict(
                status="unavailable",
                text="",
                message="Local transcription requires whisper.cpp and a model. Enter a transcript manually, or configure WHISPER_CPP_BIN and WHISPER_CPP_MODEL; no paid fallback is used.",
            )
        run_ffmpeg(
            [
                "-i",
                source,
                "-vn",
                "-ar",
                16000,
                "-ac",
                1,
                "-c:a",
                "pcm_s16le",
                folder / "speech.wav",
            ],
            folder,
        )
        result = subprocess.run(
            [
                binary,
                "-m",
                model,
                "-f",
                str(folder / "speech.wav"),
                "-oj",
                "-of",
                str(folder / "transcript"),
            ],
            capture_output=True,
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = folder / "transcript.json"
        if result.returncode or not output.is_file():
            raise RuntimeError(
                "whisper.cpp did not produce a transcript. Check the configured executable and model."
            )
        data = read_json(output)
        return dict(
            status="transcribed",
            text=" ".join(
                item.get("text", "").strip() for item in data.get("transcription", [])
            ),
            model=Path(model).name,
        )

    def analyze(self, source, kind, probe):
        source = Path(source).resolve()
        binary, model = transcription_config(self.keys)
        token = digest(
            [
                "local-analysis-v3",
                file_hash(source),
                kind,
                file_hash(binary) if binary and Path(binary).is_file() else None,
                file_hash(model) if model and Path(model).is_file() else None,
            ]
        )
        folder = self.folder / token
        folder.mkdir(parents=True, exist_ok=True)
        cache = folder / "analysis.json"
        if cache.exists():
            return dict(read_json(cache), cache_hit=True)
        result = dict(
            file_hash=file_hash(source),
            source=str(source),
            kind=kind,
            cache_key=token,
            cache_hit=False,
        )
        if kind == "text":
            result["text"] = source.read_text(encoding="utf-8")[:20000]
        elif kind == "image":
            with Image.open(source) as image:
                result.update(
                    size=list(image.size),
                    colour=list(image.convert("RGB").resize((1, 1)).getpixel((0, 0))),
                )
                image.thumbnail((768, 768))
                image.convert("RGB").save(folder / "preview.jpg")
                result["frames"] = [str(folder / "preview.jpg")]
        else:
            info = probe(source)
            result["duration"] = float(info["format"]["duration"])
            if result["duration"] > 180:
                raise ValueError(
                    "Use reference audio/video up to three minutes for this demo."
                )
            if kind == "video":
                result["frames"] = []
                for i, fraction in enumerate([0, 0.25, 0.5, 0.75]):
                    path = folder / f"frame-{i}.jpg"
                    run_ffmpeg(
                        [
                            "-ss",
                            result["duration"] * fraction,
                            "-i",
                            source,
                            "-frames:v",
                            1,
                            "-vf",
                            "scale=512:-2",
                            path,
                        ],
                        folder,
                    )
                    result["frames"].append(str(path))
            if any(stream["codec_type"] == "audio" for stream in info["streams"]):
                result["transcript"] = self.transcribe(source, folder)
            else:
                result["transcript"] = dict(status="no_audio", text="")
        save_json(cache, result)
        return result
