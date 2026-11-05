"""Explicit, checksum-pinned Windows x64 whisper.cpp demo installation.

Uses only Python's standard library. No installation/download happens at app startup.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "v1.9.2"
ARCHIVE_SHA = "49dcc16de826f20bd53d44f947a1ae49dfa81f86cad67a64d80820cb192d674a"
MODEL_SHA = "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f"
MODEL_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download(url, path, expected):
    if path.is_file() and sha256(path) == expected:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    request = urllib.request.Request(
        url, headers={"User-Agent": "CFC-explicit-local-setup"}
    )
    with (
        urllib.request.urlopen(request, timeout=60) as source,
        partial.open("wb") as output,
    ):
        size = 0
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > 100 * 1024 * 1024:
                raise RuntimeError("Download exceeded the expected demo asset limit.")
            output.write(chunk)
    if sha256(partial) != expected:
        raise RuntimeError(
            "Checksum mismatch; downloaded file was not installed: " + path.name
        )
    partial.replace(path)


def install(destination):
    if platform.system() != "Windows" or platform.machine().lower() not in [
        "amd64",
        "x86_64",
    ]:
        raise RuntimeError(
            "This helper installs Windows x64 only; configure another whisper.cpp build manually."
        )
    destination = destination.resolve()
    archive = destination / "whisper-bin-x64.zip"
    model = destination / "ggml-tiny.en.bin"
    download(
        f"https://github.com/ggml-org/whisper.cpp/releases/download/{RELEASE}/whisper-bin-x64.zip",
        archive,
        ARCHIVE_SHA,
    )
    download(
        f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{MODEL_REVISION}/ggml-tiny.en.bin",
        model,
        MODEL_SHA,
    )
    binaries = destination / RELEASE
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            if (
                not (binaries / member.filename)
                .resolve()
                .is_relative_to(binaries.resolve())
            ):
                raise RuntimeError("Unsafe archive path; extraction stopped.")
        package.extractall(binaries)
    binary = next(binaries.rglob("whisper-cli.exe"))
    check = subprocess.run(
        [str(binary), "--help"],
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if check.returncode:
        raise RuntimeError(
            "Installed whisper-cli could not start; inspect its DLL/runtime dependencies."
        )
    result = dict(
        release=RELEASE,
        binary=str(binary),
        model=str(model),
        archive_sha256=ARCHIVE_SHA,
        model_sha256=MODEL_SHA,
        model_revision=MODEL_REVISION,
        language="English",
    )
    (destination / "install.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path, default=ROOT / "data" / "tools" / "whisper"
    )
    args = parser.parse_args()
    print(
        "Downloading about 86 MB of pinned local tools/models, then checking the executable.",
        flush=True,
    )
    print(json.dumps(install(args.destination), indent=2))
