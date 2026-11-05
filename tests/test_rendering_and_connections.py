import numpy as np
import pytest
from types import SimpleNamespace
import sys
from video_agent.config import Settings
from video_agent.service import CampaignService
from video_agent.connections import redact, check_provider


def test_ort_telemetry_is_disabled_before_session_creation(monkeypatch):
    from video_agent.rendering.voice import load_kokoro

    calls = []

    def session(*args, **kwargs):
        assert calls == ["disabled"]
        calls.append("session")
        return object()

    fake_ort = SimpleNamespace(
        disable_telemetry_events=lambda: calls.append("disabled"),
        SessionOptions=SimpleNamespace,
        InferenceSession=session,
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    fake_kokoro = SimpleNamespace(
        Kokoro=SimpleNamespace(from_session=lambda *a: "engine")
    )
    monkeypatch.setitem(sys.modules, "kokoro_onnx", fake_kokoro)
    load_kokoro.cache_clear()
    try:
        assert load_kokoro("unused-test-path") == "engine"
        assert calls == ["disabled", "session"]
    finally:
        load_kokoro.cache_clear()


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


def test_local_voice_engine_is_available_in_app(tmp_path, monkeypatch):
    from video_agent.config import MODEL_FILES
    from test_workflow import make_campaign

    settings = Settings(data_dir=tmp_path)
    if any(not (settings.model_dir / name).is_file() for name in MODEL_FILES):
        pytest.skip(
            "Optional local voice integration check requires cached model files"
        )
    api = CampaignService(settings)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("Cached inference must not request network resources")
        ),
    )
    try:
        cid = make_campaign(api)
        engine = api.runtime(cid).renderer.notebook_kokoro()
        samples, rate = engine.create(
            "A local voice check.", voice="af_heart", lang="en-us"
        )
        assert rate == 24000 and len(samples) > 2400 and np.isfinite(samples).all()
    finally:
        api.close()
