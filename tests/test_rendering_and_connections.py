import numpy as np
import pytest
from video_agent.config import Settings
from video_agent.service import CampaignService
from video_agent.connections import redact, check_provider


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
