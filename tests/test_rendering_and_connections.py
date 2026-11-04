from pathlib import Path
import numpy as np
import pytest
from video_agent.config import Settings, LEGACY_ID, LEGACY_DIR, ROOT
from video_agent.service import CampaignService
from video_agent.connections import redact, check_provider


@pytest.mark.skipif(
    not (LEGACY_DIR / "plan.json").exists()
    or not (ROOT / "assets" / "airdopes_141" / "asset_manifest.json").exists(),
    reason="Optional original notebook fixture is not part of the source repository",
)
def test_rendered_frames_and_existing_video_validation(tmp_path):
    api = CampaignService(Settings(data_dir=tmp_path), import_notebook=True)
    try:
        runtime = api.runtime(LEGACY_ID)
        state = api.snapshot(LEGACY_ID)["state"]
        photos = runtime.renderer.identity_images()
        for ad in state["plan"]["ads"]:
            cues, clock = [], 0
            for scene in ad["scenes"]:
                cues.append(
                    dict(
                        start=clock,
                        end=clock + scene["seconds"],
                        caption=scene["caption"],
                    )
                )
                clock += scene["seconds"]
            for second in [0.0, 6.0, 19.0]:
                import itertools

                readers = {
                    "commute_walk": itertools.repeat(
                        bytes([100, 115, 130]) * (540 * 960)
                    )
                }
                frame = runtime.renderer.concept_frame(
                    ad, cues, photos, second, readers
                )
                assert frame.size == (1080, 1920) and frame.mode == "RGB"
        quality = runtime.renderer.validate_video(
            Path(state["sample"]["video"]), tmp_path
        )
        assert quality["full_audio_video_decode"] and quality["frames"] == 600
        runtime.renderer.make_music(tmp_path, state["plan"]["ads"][0])
        import soundfile as sf

        music, rate = sf.read(tmp_path / "music-original.wav")
        assert rate == 24000 and len(music) == 480000 and np.isfinite(music).all()
    finally:
        api.close()


def test_credentials_are_redacted():
    key = "secret-provider-key-12345"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoxfQ.signature"
    safe = redact(
        {
            "api_key": key,
            "url": "https://example.test/video?token=" + jwt,
            "message": key,
            "usage": {"tokens": 12},
        },
        {"GEMINI_API_KEY": key},
    )
    assert key not in str(safe) and jwt not in str(safe)
    assert safe["usage"]["tokens"] == 12


def test_api_check_displays_real_error_safely(monkeypatch):
    class Response:
        status_code, ok = 401, False

        def json(self):
            return {"detail": {"message": "Missing voices_read permission"}}

    monkeypatch.setattr(
        "video_agent.connections.requests.get", lambda *a, **k: Response()
    )
    result = check_provider("ElevenLabs", {"ELEVENLABS_API_KEY": "private"})
    assert result["status"] == 401 and not result["ok"]
    assert "voices_read" in result["body"]["detail"]["message"]


def test_local_voice_engine_is_available_in_app(tmp_path):
    from video_agent.config import MODEL_FILES
    from test_workflow import make_campaign

    settings = Settings(data_dir=tmp_path)
    if any(not (settings.model_dir / name).is_file() for name in MODEL_FILES):
        pytest.skip(
            "Optional local voice integration check requires cached model files"
        )
    api = CampaignService(settings)
    try:
        cid = make_campaign(api)
        engine = api.runtime(cid).renderer.notebook_kokoro()
        samples, rate = engine.create(
            "A local voice check.", voice="af_heart", lang="en-us"
        )
        assert rate == 24000 and len(samples) > 2400 and np.isfinite(samples).all()
    finally:
        api.close()
