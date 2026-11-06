"""Explicit, isolated ComfyUI setup. No application dependencies are changed.

Requires uv and git on PATH. Downloads weights only with --download-models.
Installation is not inference verification: run verify_comfy_workflow.py next.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import subprocess
import sys
import threading
import time

import requests

COMFY_REV = "4ac1c173afd42f1141dcebace42d8091998d3b8c"
GGUF_REV = "6ea2651e7df66d7585f6ffee804b20e92fb38b8a"
MODEL_REV = "5483c60ee17af446cbbdb95a939946ae4747b648"
TEXT_REV = "005a6ea51a7d0b84d677b3e633bb52a8c85a83d9"
MODELS = [
    (
        "vae",
        "city96/LTX-Video-gguf",
        MODEL_REV,
        "LTX-Video-VAE-BF16.safetensors",
        838411946,
        "17d8f8979d2303898b7320d7fbbd0b0f07cff2fcb86acae53653de4948a1ee92",
    ),
    (
        "unet",
        "city96/LTX-Video-gguf",
        MODEL_REV,
        "ltx-video-2b-v0.9-Q3_K_S.gguf",
        984805888,
        "55385a1796768b6038adf56f9f19ac576465f4be75edfe4b178784ede6ce7818",
    ),
    (
        "text_encoders",
        "city96/t5-v1_1-xxl-encoder-gguf",
        TEXT_REV,
        "t5-v1_1-xxl-encoder-Q3_K_S.gguf",
        2099467456,
        "31ebe18e901bfb6e5709a20ec1c95fce29bce2b9545073231e0f909a53239f5c",
    ),
]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(*args, cwd=None):
    subprocess.run(
        list(map(str, args)),
        cwd=cwd,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def checkout(url, folder, revision):
    if folder.exists():
        actual = subprocess.check_output(
            ["git", "-C", str(folder), "rev-parse", "HEAD"],
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).strip()
        if actual != revision:
            raise ValueError(
                "Existing checkout differs. Use a fresh --root: " + str(folder)
            )
        return
    run("git", "clone", "--no-checkout", "--filter=blob:none", url, folder)
    run("git", "fetch", "--depth", "1", "origin", revision, cwd=folder)
    run("git", "checkout", "--detach", revision, cwd=folder)


def download_model(root, specification):
    role, repo, revision, name, size, expected = specification
    target = root / "models" / role / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.stat().st_size != size or sha256(target) != expected:
            raise ValueError(
                "Existing model checksum differs; refusing to replace " + str(target)
            )
        return
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{name}"
    parts = target.parent / (name + ".download-parts")
    parts.mkdir(exist_ok=True)
    chunk_size = 2 * 1024 * 1024
    # Resolve once, then reuse connections to the signed CDN URL for byte ranges.
    response = requests.get(url, headers={"Range": "bytes=0-0"}, timeout=(15, 30))
    response.raise_for_status()
    cdn_url = response.url
    local = threading.local()

    def fetch(start):
        end = min(start + chunk_size, size) - 1
        part = parts / str(start)
        if part.is_file() and part.stat().st_size == end - start + 1:
            return
        for attempt in range(3):
            try:
                if not hasattr(local, "session"):
                    local.session = requests.Session()
                with local.session.get(
                    cdn_url,
                    headers={"Range": f"bytes={start}-{end}"},
                    stream=True,
                    timeout=(15, 20),
                ) as response:
                    response.raise_for_status()
                    if (
                        response.status_code != 206
                        or response.headers.get("Content-Range")
                        != f"bytes {start}-{end}/{size}"
                    ):
                        raise ValueError("Incorrect byte range for " + name)
                    body = bytearray()
                    deadline = time.monotonic() + 90
                    for chunk in response.iter_content(64 * 1024):
                        body.extend(chunk)
                        if len(body) > end - start + 1 or time.monotonic() > deadline:
                            raise ValueError("Byte range exceeded its size/time bound")
                if len(body) != end - start + 1:
                    raise ValueError("Incomplete byte range for " + name)
                part.write_bytes(body)
                return
            except (requests.RequestException, ValueError):
                if attempt == 2:
                    return start
                time.sleep(1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        failed = [
            offset
            for offset in pool.map(fetch, range(0, size, chunk_size))
            if offset is not None
        ]
    if failed:
        raise RuntimeError(
            f"{len(failed)} download chunks failed for {name}. Rerun to resume saved chunks."
        )
    temporary = target.with_suffix(target.suffix + ".partial")
    with temporary.open("wb") as destination:
        for start in range(0, size, chunk_size):
            destination.write((parts / str(start)).read_bytes())
    if sha256(temporary) != expected:
        raise ValueError("Downloaded model checksum failed: " + str(temporary))
    temporary.replace(target)
    print("Verified model:", target, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("tmp/local-comfy"))
    parser.add_argument("--device", choices=["cpu", "cuda"], required=True)
    parser.add_argument("--download-models", action="store_true")
    parser.add_argument(
        "--include-gallery",
        action="store_true",
        help="Also download the optional template gallery's example media bundles",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    comfy = root / "ComfyUI"
    checkout("https://github.com/Comfy-Org/ComfyUI.git", comfy, COMFY_REV)
    checkout(
        "https://github.com/city96/ComfyUI-GGUF.git",
        comfy / "custom_nodes" / "ComfyUI-GGUF",
        GGUF_REV,
    )
    environment = root / ("env-" + args.device)
    if not environment.exists():
        run("uv", "venv", "--python", "3.12", environment)
    python = environment / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    index = "https://download.pytorch.org/whl/" + (
        "cu130" if args.device == "cuda" else "cpu"
    )
    requirements = comfy / "requirements.txt"
    if not args.include_gallery:
        requirements = root / "requirements-server.txt"
        requirements.write_text(
            "\n".join(
                line
                for line in (comfy / "requirements.txt").read_text().splitlines()
                if not line.startswith("comfyui-workflow-templates")
            )
            + "\n",
            encoding="utf-8",
        )
    run(
        "uv",
        "pip",
        "install",
        "--python",
        python,
        "torch==2.11.0",
        "torchvision==0.26.0",
        "--index-url",
        index,
    )
    run(
        "uv",
        "pip",
        "install",
        "--python",
        python,
        "-r",
        requirements,
        "-r",
        comfy / "custom_nodes/ComfyUI-GGUF/requirements.txt",
    )
    if args.download_models:
        for model in MODELS:
            download_model(comfy, model)
    print("Setup finished. This does not establish successful video generation.")
    print("Start from", comfy, "using", python)
    print(
        "python main.py --offline --disable-all-custom-nodes --whitelist-custom-nodes ComfyUI-GGUF --disable-dynamic-vram --use-pytorch-cross-attention --fp32-vae --cache-none --preview-method none"
        + (" --cpu" if args.device == "cpu" else " --reserve-vram 0.8")
    )


if __name__ == "__main__":
    main()
