"""Startup ordering only; native-denied inference is verified separately."""

import builtins
import io
import json
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import numpy as np


def test_parent_sets_opt_out_before_starting_child(monkeypatch):
    from video_agent.rendering.voice import IsolatedKokoro

    captured = []

    def popen(*args, **kwargs):
        captured.append(kwargs["env"])
        return SimpleNamespace(
            stdout=io.StringIO('{"ok":true,"voices":["af_heart"]}\n'), poll=lambda: 0
        )

    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "0")
    monkeypatch.setattr("video_agent.rendering.voice.subprocess.Popen", popen)
    engine = IsolatedKokoro("unused-model-path", "unused-python")
    assert engine.info["ok"] and captured[0]["ORT_DISABLE_TELEMETRY"] == "1"
    assert (
        os.environ["ORT_DISABLE_TELEMETRY"] == "0"
    )  # No unrelated parent environment mutation.


def test_worker_disables_startup_telemetry_before_native_import(
    monkeypatch, tmp_path, capsys
):
    calls = []
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "onnxruntime":
            assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"
            calls.append("import")
        return original(name, *args, **kwargs)

    def session(*args, **kwargs):
        assert calls == ["import", "disabled"]
        calls.append("session")
        return object()

    ort = SimpleNamespace(
        disable_telemetry_events=lambda: calls.append("disabled"),
        SessionOptions=SimpleNamespace,
        InferenceSession=session,
        __version__="test",
    )
    engine = SimpleNamespace(
        create=lambda *a, **k: (np.ones(2400), 24000), get_voices=lambda: ["af_heart"]
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(
        sys.modules,
        "kokoro_onnx",
        SimpleNamespace(Kokoro=SimpleNamespace(from_session=lambda *a: engine)),
    )
    monkeypatch.setattr("importlib.metadata.version", lambda name: "test")
    monkeypatch.setattr(builtins, "__import__", importing)
    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "0")
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"operation":"close"}\n'))
    monkeypatch.setattr(sys, "argv", ["tts_worker.py", str(tmp_path)])
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/tts_worker.py"),
        run_name="__main__",
    )
    assert calls == ["import", "disabled", "session"]
    assert json.loads(capsys.readouterr().out)["telemetry_opt_out"] == "1"
