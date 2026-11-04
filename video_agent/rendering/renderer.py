"""Campaign-scoped local renderer. No Streamlit or notebook dependencies."""

from pathlib import Path
import shutil
import threading
from .audio import AudioMixin
from .visuals import VisualMixin
from .pipeline import RenderMixin
from .voice import VOICE_LOCK, ElevenLabsVoice, load_kokoro
from ..config import MODEL_FILES, RENDER_VERSION
from ..models import CampaignPlan, AdPlan
from ..validation import plan_copy_issues
from ..storage import digest, read_json, signature


class VideoRenderer(AudioMixin, VisualMixin, RenderMixin):
    def __init__(self, folder, settings, assets, keys, note):
        self.folder, self.model_dir = Path(folder), settings.model_dir
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.assets, self.keys, self.note = assets, keys, note
        self.brief = {}
        self.voice_id, self.voice_speed = "af_heart", 1.02
        self.voice_backend = "local_kokoro_notebook"
        self.voice_lock, self.render_lock = VOICE_LOCK, threading.RLock()
        self.commute_config = self.folder / "commute_inputs.json"
        self.commute_files = (
            read_json(self.commute_config) if self.commute_config.exists() else {}
        )

    def notebook_kokoro(self):
        if self.voice_backend == "elevenlabs":
            return ElevenLabsVoice(
                self.keys, self.keys.get("ELEVENLABS_MODEL") or "eleven_multilingual_v2"
            )
        for name, size in MODEL_FILES.items():
            self.ensure_notebook_model(name, size)
        return load_kokoro(str(self.model_dir))

    def render(self, state, ad, repository):
        repository.require_approval(state)
        if not shutil.which("ffprobe"):
            raise RuntimeError(
                "FFprobe is missing. Install FFmpeg with FFprobe on PATH before generating video."
            )
        if ad not in state["plan"]["ads"]:
            raise ValueError("The requested script is not part of the approved plan.")
        brief, version = state["brief"], state["version"]
        checked = CampaignPlan(summary="Selected ad", ads=[AdPlan.model_validate(ad)])
        issues = plan_copy_issues(checked, dict(brief, count=1))
        if issues:
            raise ValueError("; ".join(issues[:3]))
        self.commute_files = (
            read_json(self.commute_config) if self.commute_config.exists() else {}
        )
        used = {key for scene in ad["scenes"] for key in scene["asset_ids"]}
        if brief.get("schema_version", 1) < 2 and self.commute_files.get(
            "commute_walk"
        ):
            self.commute_files.setdefault(
                "commute_transit", self.commute_files["commute_walk"]
            )
        for key in used:
            if (
                key.startswith(("commute_", "lifestyle_"))
                and not Path(self.commute_files.get(key, "__missing__")).is_file()
            ):
                raise ValueError(
                    "Upload the planned lifestyle footage before rendering: " + key
                )
        for key, expected in brief.get("asset_hashes", {}).items():
            if self.assets.get(key, {}).get("sha256") != expected:
                raise ValueError(
                    "Product assets changed. Review and approve the brief again."
                )
        self.voice_backend = brief.get("voice_backend", "local_kokoro_notebook")
        self.voice_id = brief.get("voice_id") or (
            "af_heart" if self.voice_backend != "elevenlabs" else ""
        )
        token = digest(
            [
                RENDER_VERSION,
                signature(state),
                ad,
                self.voice_backend,
                self.voice_id,
                self.voice_speed,
                self.commute_files,
            ]
        )
        folder = self.folder / "renders" / token
        folder.mkdir(parents=True, exist_ok=True)
        with self.render_lock:
            self.brief = brief
            self.identity_images()
            if (folder / "result.json").exists():
                result = read_json(folder / "result.json")
                if Path(result["video"]).exists():
                    return result
            self.note("Generating voice and soundtrack for " + ad["title"])
            return self.build_video(brief, ad, version, folder)
