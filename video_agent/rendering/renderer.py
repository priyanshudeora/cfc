"""Approval-checked local editing with independent scene and speech caches."""

from pathlib import Path
import threading
from .audio import AudioMixin
from .visuals import VisualMixin
from .pipeline import RenderMixin
from .voice import VOICE_LOCK, ElevenLabsVoice, local_voice
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
            import sys

            self.tts_runtime = dict(
                python=sys.version.split()[0],
                separate_worker=False,
                remote_inference=True,
            )
            return ElevenLabsVoice(
                self.keys,
                self.brief.get("voice_model")
                or self.keys.get("ELEVENLABS_MODEL")
                or "eleven_multilingual_v2",
            )
        from ..voice_setup import validate_voice_models

        validate_voice_models(self.model_dir)
        from ..cache import file_hash
        from ..storage import digest

        self.voice_model_hash = digest(
            [file_hash(self.model_dir / name) for name in MODEL_FILES]
        )
        engine = local_voice(self.model_dir, self.keys)
        self.tts_runtime = dict(
            python=engine.info["python"],
            separate_worker=True,
            kokoro_onnx=engine.info["kokoro_onnx"],
            onnxruntime=engine.info["onnxruntime"],
            cached_audio_may_be_reused=True,
        )
        return engine

    def generated_source(self, scene, key):
        from ..generation_contract import generation_key

        record = getattr(self, "generated_records", {}).get(
            generation_key(scene, self.brief)
        )
        if not record:
            raise ValueError(
                "The approved generated footage has not finished preparation. No editing-only substitution is made."
            )
        return record["path"]

    def render(self, state, ad, repository):
        repository.require_approval(state)
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
            self.generated_records = {}
            generated = [
                s
                for s in parsed.model_dump()["scenes"]
                if s["edit"]["mode"] != "reused"
            ]
            from ..preflight import (
                editing_readiness,
                validate_assets,
                generation_preflight,
            )

            self.note("Preflight: editing tools and product assets")
            editing = editing_readiness()
            if not editing["available"]:
                raise ValueError(editing["message"])
            stage_ads = (
                state["plan"]["ads"][:1] if index == 0 else state["plan"]["ads"][1:]
            )
            stage_scenes = [
                scene for stage_ad in stage_ads for scene in stage_ad["scenes"]
            ]
            validate_assets(brief, self.assets, self.commute_files, stage_scenes)
            self.identity_images()
            self.note(
                "Preflight: initialize selected voice before any footage submission"
            )
            engine = self.notebook_kokoro()
            if self.voice_backend == "elevenlabs":
                if not engine.key or not self.voice_id:
                    raise ValueError(
                        "ElevenLabs requires an API key and selected voice ID before footage generation."
                    )
            elif self.voice_id not in engine.info["voices"]:
                raise ValueError(
                    "Selected local voice ID is unavailable: " + self.voice_id
                )
            if generated:
                from ..generation import ComfyFootage
                from ..generation_contract import generation_key

                self.generation_adapter = ComfyFootage(
                    self.keys,
                    repository.control,
                    state["campaign_id"],
                    self.settings.gpu_workers,
                    assets=self.assets,
                    brief=brief,
                    note=self.note,
                )
                self.note(
                    "Preflight: ComfyUI profile, stage capability and available allowance"
                )
                required = "; ".join(
                    f"{a['title']} / scene {i + 1}"
                    for a in stage_ads
                    for i, s in enumerate(a["scenes"])
                    if s["edit"]["mode"] != "reused"
                )
                try:
                    generation_preflight(
                        self.generation_adapter,
                        stage_scenes,
                        self.media_cache,
                        brief,
                        "sample" if index == 0 else "batch",
                    )
                except Exception as error:
                    raise type(error)(
                        f"Required AI scenes ({required}): {error}"
                    ) from error
                # Preparation happens before FFmpeg/speech cache tasks. A slow or
                # interrupted provider poll does not exhaust parent render retries.
                for i, scene in enumerate(generated, 1):
                    source_key = generation_key(scene, brief)
                    scene_label = f"{ad['title']} / scene {parsed.model_dump()['scenes'].index(scene) + 1}"
                    self.note(
                        f"Preparing AI footage {i}/{len(generated)} — {scene_label}"
                    )
                    try:
                        self.generation_adapter.generate(
                            scene, source_key, self.media_cache
                        )
                    except Exception as error:
                        raise type(error)(
                            f"AI scene {scene_label}: {error}. Completed clips are retained for Retry; switching to supplied-image animation requires editing Creation/scene mode and fresh approval."
                        ) from error
                    self.generated_records[source_key] = dict(
                        self.generation_adapter.last_result
                    )
                    self.note(f"Footage ready: {i}/{len(generated)}")
            canonical = parsed.model_dump()

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
