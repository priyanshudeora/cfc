"""Approval-checked local editing with independent scene and speech caches."""

from pathlib import Path
import shutil
import threading
from .audio import AudioMixin
from .visuals import VisualMixin
from .pipeline import RenderMixin
from .voice import VOICE_LOCK, ElevenLabsVoice, load_kokoro
from ..config import MODEL_FILES
from ..models import CampaignPlan, AdPlan
from ..validation import plan_copy_issues
from ..storage import read_json, signature
from ..timeline import ad_key
from ..cache import MediaCache
from ..control import DEFAULT_LIMITS


class VideoRenderer(AudioMixin, VisualMixin, RenderMixin):
    def __init__(self, folder, settings, assets, keys, note):
        self.folder, self.model_dir, self.settings = (
            Path(folder),
            settings.model_dir,
            settings,
        )
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.assets, self.keys, self.note, self.brief = assets, keys, note, {}
        self.voice_id, self.voice_speed, self.voice_backend = (
            "af_heart",
            1.02,
            "local_kokoro_notebook",
        )
        self.voice_lock, self.render_lock = VOICE_LOCK, threading.RLock()
        self.commute_config = self.folder / "commute_inputs.json"
        self.commute_files = (
            read_json(self.commute_config) if self.commute_config.exists() else {}
        )
        self.generation_adapter = None

    def notebook_kokoro(self):
        if self.voice_backend == "elevenlabs":
            return ElevenLabsVoice(
                self.keys,
                self.brief.get("voice_model")
                or self.keys.get("ELEVENLABS_MODEL")
                or "eleven_multilingual_v2",
            )
        for name, size in MODEL_FILES.items():
            self.ensure_notebook_model(name, size)
        from ..cache import file_hash
        from ..storage import digest

        self.voice_model_hash = digest(
            [file_hash(self.model_dir / name) for name in MODEL_FILES]
        )
        return load_kokoro(str(self.model_dir))

    def generated_source(self, scene, key):
        if not self.generation_adapter:
            raise ValueError(
                "New footage generation is unavailable. Configure and verify local ComfyUI, or select reused image-based motion graphics."
            )
        return self.generation_adapter.generate(scene, key, self.media_cache)

    def render(self, state, ad, repository):
        repository.require_approval(state)
        if not shutil.which("ffprobe"):
            raise RuntimeError(
                "Install FFmpeg with FFprobe on PATH before generating video."
            )
        if ad not in state["plan"]["ads"]:
            raise ValueError("This video is outside the approved plan.")
        index = state["plan"]["ads"].index(ad)
        brief, version = state["brief"], state["version"]
        key = ad_key(ad, brief)
        scope = repository.control.require_grant(
            state["campaign_id"],
            "sample" if index == 0 else "batch",
            signature(state),
            key,
        )
        parsed = AdPlan.model_validate(ad)
        issues = plan_copy_issues(
            CampaignPlan(summary="Selected ad", ads=[parsed]), dict(brief, count=1)
        )
        if issues:
            raise ValueError("; ".join(issues[:4]))
        self.commute_files = (
            read_json(self.commute_config) if self.commute_config.exists() else {}
        )
        for asset, expected in brief.get("asset_hashes", {}).items():
            if asset in self.commute_files:
                from ..cache import file_hash

                if file_hash(self.commute_files[asset]) != expected:
                    raise ValueError("Planned footage changed; review the brief again.")
                continue
            if self.assets.get(asset, {}).get("sha256") != expected:
                raise ValueError("Assets changed; review the brief again.")
        with self.render_lock:
            self.brief = brief
            self.voice_backend = brief.get("voice_backend", "local_kokoro_notebook")
            self.voice_id = brief.get("voice_id") or "af_heart"
            self.voice_speed = brief.get("voice_speed", 1.02)
            self.media_cache = MediaCache(
                self.folder / "cache",
                repository.control,
                state["campaign_id"],
                scope.get("limits", DEFAULT_LIMITS),
                self.settings.cpu_workers,
                self.settings.provider_workers,
                brief.get("usage_rates"),
            )
            if self.keys.get("COMFY_WORKFLOW"):
                from ..generation import ComfyFootage

                self.generation_adapter = ComfyFootage(
                    self.keys,
                    repository.control,
                    state["campaign_id"],
                    self.settings.gpu_workers,
                )
                if any(
                    scene["edit"]["mode"] != "reused"
                    for scene in parsed.model_dump()["scenes"]
                ):
                    from ..cache import file_hash

                    if brief.get("generation", {}).get("workflow_sha256") != file_hash(
                        self.keys["COMFY_WORKFLOW"]
                    ):
                        raise ValueError(
                            "The local generation workflow changed. Save and approve its new scope before submitting footage jobs."
                        )
            canonical = parsed.model_dump()
            self.identity_images()
            self.notebook_kokoro()  # Resolve the actual model before forming its media dependency key.

            def build(folder):
                return self.build_video(brief, canonical, version, folder)

            def verify(result):
                folder = Path(result["folder"])
                info = self.probe(folder / "final.mp4")
                if (
                    abs(float(info["format"]["duration"]) - 20) > 0.05
                    or result["quality"]["status"] != "passed"
                ):
                    raise ValueError("Cached final video is invalid.")

            # The bundle depends on actual uploaded footage bytes as well as the plan.
            from ..cache import file_hash
            from ..storage import digest

            used = {a for scene in ad["scenes"] for a in scene["asset_ids"]}
            media_key = digest(
                [
                    key,
                    getattr(self, "voice_model_hash", brief.get("voice_model")),
                    {
                        a: file_hash(p)
                        for a, p in self.commute_files.items()
                        if a in used
                    },
                ]
            )
            result, hit = self.media_cache.run(
                "video", media_key, build, verify, {"render_seconds": 40}
            )
            delivered = self.deliver(
                canonical, version, Path(result["folder"]), result["quality"]
            )
            delivered.update(
                content_key=key,
                cache_hit=hit,
                editing_engine="FFmpeg",
                human_review_required=True,
                contact_sheet=str(Path(result["folder"]) / "review-contact-sheet.jpg"),
            )
            self.note(
                "Reused verified video"
                if hit
                else "FFmpeg export ready for human media review"
            )
            return delivered
