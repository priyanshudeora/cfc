import pytest
from video_agent.storage import read_json, save_json


def test_transient_windows_lock_preserves_old_json_until_atomic_replacement(
    tmp_path, monkeypatch
):
    path = tmp_path / "job.json"
    save_json(path, {"status": "running"})
    from video_agent import storage

    original = storage.os.replace
    attempts = []

    def busy_then_replace(source, destination):
        attempts.append(1)
        if len(attempts) < 3:
            assert read_json(path) == {"status": "running"}
            error = PermissionError("Windows sharing violation")
            error.winerror = 32
            raise error
        return original(source, destination)

    monkeypatch.setattr(storage.os, "replace", busy_then_replace)
    monkeypatch.setattr(storage.time, "sleep", lambda seconds: None)
    save_json(path, {"status": "finished"})
    assert read_json(path) == {"status": "finished"} and len(attempts) == 3
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("winerror", [None, 5])
def test_permanent_permission_error_is_bounded_and_preserves_old_file(
    tmp_path, monkeypatch, winerror
):
    path = tmp_path / "job.json"
    save_json(path, {"status": "saved"})
    from video_agent import storage

    attempts = []

    def denied(*args):
        attempts.append(1)
        error = PermissionError("Access denied")
        if winerror is not None:
            error.winerror = winerror
        raise error

    monkeypatch.setattr(storage.os, "replace", denied)
    monkeypatch.setattr(storage.time, "sleep", lambda seconds: None)
    with pytest.raises(PermissionError):
        save_json(path, {"status": "new"})
    assert len(attempts) == 1 if winerror is None else 2 <= len(attempts) <= 10
    assert read_json(path) == {"status": "saved"}
    assert not list(tmp_path.glob("*.tmp"))
