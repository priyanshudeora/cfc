"""Serialized campaign jobs with progress that survives frontend reruns."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from collections import deque
import re
import threading
import uuid
from .storage import now, save_json, read_json


@dataclass
class Job:
    id: str
    title: str
    status: str = "running"
    progress: float = 0.0
    error: str = ""
    started_at: str = field(default_factory=now)
    events: deque = field(default_factory=lambda: deque(maxlen=80))
    future: object = None

    def public(self):
        return {
            k: getattr(self, k)
            for k in ["id", "title", "status", "progress", "error", "started_at"]
        } | {"events": list(self.events)}


class JobManager:
    def __init__(self, repository, scrub=str):
        self.repository = repository
        self.scrub = scrub
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="video-studio")
        self.jobs, self.lock = {}, threading.RLock()

    def busy(self, cid):
        with self.lock:
            job = self.jobs.get(cid)
            return bool(job and job.status == "running")

    def get(self, cid):
        with self.lock:
            if cid in self.jobs:
                return self.jobs[cid].public()
        path = self.repository.folder(cid) / "last_job.json"
        if path.exists():
            record = read_json(path)
            if record["status"] == "running":
                record.update(
                    status="interrupted",
                    error="The app restarted. Retry the interrupted step to continue.",
                )
            return record
        return None

    def note(self, cid, message):
        with self.lock:
            job = self.jobs.get(cid)
            if not job:
                return
            job.events.append(self.scrub(str(message)))
            match = re.search(r"Encoding sample: (\d+)/20", str(message))
            if match:
                job.progress = min(0.98, int(match.group(1)) / 20)

    def submit(self, cid, title, function):
        with self.lock:
            if self.busy(cid):
                raise ValueError("This campaign already has a job running.")
            job = Job(uuid.uuid4().hex, title)
            self.jobs[cid] = job
            save_json(self.repository.folder(cid) / "last_job.json", job.public())

            def run():
                try:
                    function()
                except Exception as error:
                    with self.lock:
                        job.error, job.status = self.scrub(str(error))[:600], "failed"
                        job.events.append(job.error)
                else:
                    with self.lock:
                        job.status, job.progress = "finished", 1.0
                finally:
                    save_json(
                        self.repository.folder(cid) / "last_job.json", job.public()
                    )

            job.future = self.pool.submit(run)
            return job.id

    def wait(self, cid, timeout=120):
        with self.lock:
            future = self.jobs[cid].future
        future.result(timeout=timeout)

    def resolve(self, cid, message):
        """Clear a recovered failure while retaining its activity history."""
        with self.lock:
            job = self.jobs.get(cid)
            if not job:
                record = self.get(cid)
                if record and record["status"] in ["failed", "interrupted"]:
                    record.update(status="finished", progress=1.0, error="")
                    record["events"].append(message)
                    save_json(self.repository.folder(cid) / "last_job.json", record)
                return
            if job.status not in ["failed", "interrupted"]:
                return
            job.status, job.progress, job.error = "finished", 1.0, ""
            job.events.append(message)
            save_json(self.repository.folder(cid) / "last_job.json", job.public())

    def close(self):
        self.pool.shutdown(wait=True)
