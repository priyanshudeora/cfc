"""Verified, campaign-scoped content caches with durable per-task accounting."""

from pathlib import Path
import hashlib
import time
from contextlib import nullcontext
from .storage import read_json, save_json


def file_hash(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class MediaCache:
    def __init__(
        self,
        folder,
        control,
        cid,
        limits,
        cpu_workers=2,
        provider_workers=1,
        rates=None,
    ):
        self.folder, self.control, self.cid, self.limits = (
            Path(folder),
            control,
            cid,
            limits,
        )
        self.folder.mkdir(parents=True, exist_ok=True)
        self.cpu_workers, self.provider_workers = cpu_workers, provider_workers
        self.rates = rates or {}

    def run(self, kind, key, build, verify, usage=None, external=False):
        folder = self.folder / kind / key
        folder.mkdir(parents=True, exist_ok=True)
        manifest = folder / "cache.json"
        lock = self.cid + ":" + kind + ":" + key
        with self.control.exclusive(lock):
            self.control.check_cancelled(self.cid)
            if manifest.exists():
                entry = read_json(manifest)
                try:
                    if all(
                        Path(path).is_file() and file_hash(path) == sha
                        for path, sha in entry["hashes"].items()
                    ):
                        verify(entry["result"])
                        return entry["result"], True
                except (ValueError, OSError, RuntimeError, KeyError):
                    pass
            self.control.check_cancelled(self.cid)
            task_key = kind + ":" + key
            self.control.task_start(self.cid, task_key, kind)
            task = self.control.task(self.cid, task_key)
            allowance_key = task_key + ":" + str(task["attempts"])
            from .usage import charge

            usage = charge(usage or {}, self.rates)
            try:
                self.control.reserve(
                    self.cid, allowance_key, usage or {}, self.limits, scope="media"
                )
            except ValueError as error:
                self.control.task_update(self.cid, task_key, "paused", error=str(error))
                raise
            if external:
                self.control.task_update(self.cid, task_key, "submitting")
            started = time.monotonic()
            try:
                resource = (
                    self.control.resource("cpu", self.cpu_workers, self.cid)
                    if kind == "scene"
                    else self.control.resource(
                        "provider", self.provider_workers, self.cid
                    )
                    if external and kind == "speech"
                    else nullcontext()
                )
                with resource:
                    result = build(folder)
                verify(result)
                paths = result.pop("_files")
                save_json(
                    manifest,
                    dict(
                        result=result,
                        hashes={str(path): file_hash(path) for path in paths},
                    ),
                )
                self.control.task_update(self.cid, task_key, "complete", output=result)
                self.control.settle(self.cid, allowance_key, usage or {})
                return result, False
            except BaseException as error:
                status = "unknown" if external else "failed"
                self.control.task_update(self.cid, task_key, status, error=str(error))
                if not external:
                    actual = dict(usage or {})
                    if "render_seconds" in actual:
                        actual["render_seconds"] = min(
                            actual["render_seconds"],
                            max(1, round(time.monotonic() - started)),
                        )
                    self.control.settle(self.cid, allowance_key, actual)
                raise
