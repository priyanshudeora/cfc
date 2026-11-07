"""Create a separate supported Kokoro environment, with a hash-locked public install."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def setup(environment=None, download=False, model_dir=None, note=print):
    uv = shutil.which("uv")
    if not uv:
        raise ValueError(
            "Install the setup tool first: python -m pip install uv==0.12.19"
        )
    environment = Path(environment or ROOT / ".venv-kokoro").resolve()
    python = environment / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    marker = environment / "cfc-tts-runtime.json"
    commands = []
    if python.is_file():
        if not marker.is_file():
            raise ValueError(
                "Existing environment is not owned by this installer. Choose a fresh --environment path; its packages were not changed."
            )
        version = subprocess.check_output(
            [
                str(python),
                "-c",
                "import sys; print('.'.join(map(str,sys.version_info[:2])))",
            ],
            text=True,
        ).strip()
        if version != "3.12":
            raise ValueError(
                "Use a fresh Python 3.12 voice environment; existing environment was not changed."
            )
    else:
        if environment.exists():
            raise ValueError(
                "The target exists without Python; choose a fresh --environment path."
            )
        commands.append([uv, "venv", "--python", "3.12", str(environment)])
    commands.append(
        [
            uv,
            "pip",
            "sync",
            "--no-cache",
            "--require-hashes",
            "--python",
            str(python),
            str(ROOT / "requirements-tts.txt"),
        ]
    )
    for command in commands:
        note(
            "Creating Python 3.12 voice environment"
            if "venv" in command
            else "Installing public hash-locked speech dependencies (cache disabled)"
        )
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            for line in process.stdout:
                note(line.rstrip())
            if process.wait():
                raise RuntimeError(
                    "Speech setup failed; inspect the progress above. Retry the setup command. No campaign approvals or footage were changed."
                )
        if "venv" in command:
            marker.write_text(
                json.dumps({"installer": "CFC setup_tts", "python": "3.12"})
            )
    subprocess.run(
        [uv, "pip", "check", "--python", str(python)],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if download:
        from video_agent.config import MODEL_CACHE
        from video_agent.voice_setup import download_voice_models

        download_voice_models(model_dir or MODEL_CACHE, note)
    note("Voice dependencies ready: " + str(python))
    return str(python)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--download-models", action="store_true")
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    setup(args.environment, args.download_models, args.model_dir)


if __name__ == "__main__":
    main()
