"""Transactional review authorizations, leases, tasks and usage in SQLite."""

from contextlib import contextmanager
import json
import sqlite3
import time
import uuid
import threading
from .storage import authorization_digest


DEFAULT_LIMITS = dict(
    model_tokens=64000,
    speech_characters=0,
    generation_jobs=0,
    render_seconds=300,
    cost_microusd=0,
)


class AllowanceExceeded(ValueError):
    pass


class ControlStore:
    def __init__(self, path, lease_seconds=15):
        self.path, self.lease_seconds = str(path), lease_seconds
        with self.transaction() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,cid TEXT,stage TEXT,
              checkpoint TEXT,content TEXT,actions TEXT,scope TEXT,used TEXT,receipt TEXT);
            CREATE TABLE IF NOT EXISTS leases(cid TEXT PRIMARY KEY,owner TEXT,expires REAL);
            CREATE TABLE IF NOT EXISTS tasks(cid TEXT,key TEXT,kind TEXT,status TEXT,attempts INTEGER,
              provider_id TEXT,output TEXT,error TEXT,updated REAL,PRIMARY KEY(cid,key));
            CREATE TABLE IF NOT EXISTS usage(cid TEXT,key TEXT,scope TEXT,reserved TEXT,actual TEXT,
              status TEXT,PRIMARY KEY(cid,key));
            CREATE TABLE IF NOT EXISTS flags(cid TEXT PRIMARY KEY,cancelled INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS grants(cid TEXT,stage TEXT,content TEXT,scope TEXT,
              review_id TEXT,PRIMARY KEY(cid,stage,content));
            """)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=30000")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def busy(self, cid):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM leases WHERE cid=?", (cid,)).fetchone()
            return bool(row and row["expires"] > time.time())

    def acquire(self, cid, owner):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM leases WHERE cid=?", (cid,)).fetchone()
            if row and row["expires"] > time.time() and row["owner"] != owner:
                raise ValueError("A campaign job is already running in another worker.")
            db.execute(
                "INSERT OR REPLACE INTO leases VALUES(?,?,?)",
                (cid, owner, time.time() + self.lease_seconds),
            )

    def heartbeat(self, cid, owner):
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE leases SET expires=? WHERE cid=? AND owner=?",
                (time.time() + self.lease_seconds, cid, owner),
            ).rowcount
            if not changed:
                raise ValueError("The worker lost its campaign lease.")

    def release(self, cid, owner):
        with self.transaction() as db:
            db.execute("DELETE FROM leases WHERE cid=? AND owner=?", (cid, owner))

    @contextmanager
    def exclusive(self, cid):
        owner = uuid.uuid4().hex
        self.acquire(cid, owner)
        stopped = threading.Event()

        def renew():
            while not stopped.wait(self.lease_seconds / 3):
                self.heartbeat(cid, owner)

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=2)
            self.release(cid, owner)

    @contextmanager
    def resource(self, name, limit, cid, wait_seconds=120):
        owner = uuid.uuid4().hex
        acquired = None
        for _ in range(max(1, int(wait_seconds / 0.2))):
            self.check_cancelled(cid)
            for slot in range(max(1, min(8, int(limit)))):
                key = f"resource:{name}:{slot}"
                try:
                    self.acquire(key, owner)
                    acquired = key
                    break
                except ValueError:
                    pass
            if acquired:
                break
            time.sleep(0.2)
        if not acquired:
            raise RuntimeError("Resource queue timeout: " + name)
        stopped = threading.Event()

        def renew():
            while not stopped.wait(self.lease_seconds / 3):
                self.heartbeat(acquired, owner)

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=2)
            self.release(acquired, owner)

    def issue(self, cid, stage, checkpoint, content, actions, scope):
        fingerprint = authorization_digest(content)
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM reviews WHERE cid=? AND stage=? AND checkpoint=? AND content=? AND used=''",
                (cid, stage, checkpoint, fingerprint),
            ).fetchone()
            if row:
                return row["id"]
            token = uuid.uuid4().hex
            db.execute(
                "UPDATE reviews SET used='superseded' WHERE cid=? AND stage=? AND checkpoint=? AND used=''",
                (cid, stage, checkpoint),
            )
            db.execute(
                "INSERT INTO reviews VALUES(?,?,?,?,?,?,?,'','')",
                (
                    token,
                    cid,
                    stage,
                    checkpoint,
                    fingerprint,
                    json.dumps(actions),
                    json.dumps(scope),
                ),
            )
            return token

    def revoke(self, cid):
        with self.transaction() as db:
            db.execute(
                "UPDATE reviews SET used='revoked' WHERE cid=? AND used=''", (cid,)
            )

    def consume(self, token, cid, stage, checkpoint, content, action, receipt):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM reviews WHERE id=?", (token,)).fetchone()
            if (
                not row
                or row["used"]
                or (row["cid"], row["stage"], row["checkpoint"], row["content"])
                != (cid, stage, checkpoint, authorization_digest(content))
            ):
                raise ValueError(
                    "This review is stale, already used, or belongs to another checkpoint."
                )
            if action not in json.loads(row["actions"]):
                raise ValueError("This action is not authorized by the active review.")
            db.execute(
                "UPDATE reviews SET used=?,receipt=? WHERE id=? AND used=''",
                (action, receipt, token),
            )
            scope = json.loads(row["scope"])
            if action in ["approve", "generate"]:
                grant_stage = {
                    "approve_plan": "sample",
                    "approve_sample": "batch",
                    "approve_final": "export",
                }.get(stage)
                if grant_stage:
                    db.execute(
                        "INSERT OR REPLACE INTO grants VALUES(?,?,?,?,?)",
                        (cid, grant_stage, scope["plan"], json.dumps(scope), token),
                    )
            return scope

    def receipt(
        self, token, cid, stage, action, receipt, content=None, checkpoint=None
    ):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM reviews WHERE id=?", (token,)).fetchone()
            if not row or (row["cid"], row["stage"], row["used"], row["receipt"]) != (
                cid,
                stage,
                action,
                receipt,
            ):
                raise ValueError("Missing stage-specific review authorization.")
            if content is not None and row["content"] != authorization_digest(content):
                raise ValueError("The reviewed content changed.")
            if checkpoint is not None and row["checkpoint"] != checkpoint:
                raise ValueError("The review belongs to a different checkpoint.")
            return json.loads(row["scope"])

    def require_grant(self, cid, stage, plan, ad_key=None):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM grants WHERE cid=? AND stage=? AND content=?",
                (cid, stage, plan),
            ).fetchone()
            if not row:
                raise ValueError(
                    f"Explicit {stage} authorization is required for this plan."
                )
            scope = json.loads(row["scope"])
            if ad_key and ad_key not in scope.get("ads", []):
                raise ValueError("This video falls outside the approved stage scope.")
            return scope

    def cancel(self, cid, cancelled=True):
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO flags VALUES(?,?)", (cid, int(cancelled))
            )

    def check_cancelled(self, cid):
        with self.transaction() as db:
            row = db.execute(
                "SELECT cancelled FROM flags WHERE cid=?", (cid,)
            ).fetchone()
            if row and row[0]:
                raise RuntimeError(
                    "Cancelled: queued and future work stopped; a provider request already running may finish."
                )

    def task(self, cid, key):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE cid=? AND key=?", (cid, key)
            ).fetchone()
            return dict(row) if row else None

    def tasks(self, cid):
        with self.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM tasks WHERE cid=? ORDER BY updated", (cid,)
                )
            ]

    def task_start(self, cid, key, kind, max_attempts=2):
        self.check_cancelled(cid)
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE cid=? AND key=?", (cid, key)
            ).fetchone()
            if row and row["status"] in ["submitting", "unknown"]:
                raise RuntimeError(
                    "Unknown provider outcome: reconcile the recorded request before retrying."
                )
            attempts = row["attempts"] if row else 0
            if row and row["status"] == "paused":
                attempts = max(0, attempts - 1)
            if attempts >= max_attempts:
                raise RuntimeError(
                    "Task retry limit reached; review this task before increasing its allowance."
                )
            db.execute(
                "INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    cid,
                    key,
                    kind,
                    "running",
                    attempts + 1,
                    row["provider_id"] if row else "",
                    "",
                    "",
                    time.time(),
                ),
            )

    def task_update(self, cid, key, status, *, provider_id=None, output=None, error=""):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE cid=? AND key=?", (cid, key)
            ).fetchone()
            if not row:
                raise ValueError("Unknown task.")
            db.execute(
                "UPDATE tasks SET status=?,provider_id=?,output=?,error=?,updated=? WHERE cid=? AND key=?",
                (
                    status,
                    provider_id if provider_id is not None else row["provider_id"],
                    json.dumps(output) if output is not None else row["output"],
                    error[:600],
                    time.time(),
                    cid,
                    key,
                ),
            )

    def reserve(self, cid, key, usage, limits, scope="planning"):
        with self.transaction() as db:
            previous = db.execute(
                "SELECT * FROM usage WHERE cid=? AND key=?", (cid, key)
            ).fetchone()
            if previous:
                if previous["status"] == "reserved":
                    raise AllowanceExceeded(
                        "A previous reservation has an unknown outcome; reconcile it before retrying."
                    )
                return False
            totals = {name: 0 for name in DEFAULT_LIMITS}
            for row in db.execute("SELECT * FROM usage WHERE cid=?", (cid,)):
                for name, value in json.loads(
                    row["actual"] if row["status"] == "settled" else row["reserved"]
                ).items():
                    totals[name] = totals.get(name, 0) + value
            for name, value in usage.items():
                if value < 0 or totals.get(name, 0) + value > limits.get(name, 0):
                    raise AllowanceExceeded(
                        f"Approved allowance exhausted: {name}. Review the limits before continuing."
                    )
            db.execute(
                "INSERT INTO usage VALUES(?,?,?,?,?,'reserved')",
                (cid, key, scope, json.dumps(usage), "{}"),
            )
            return True

    def settle(self, cid, key, actual):
        with self.transaction() as db:
            db.execute(
                "UPDATE usage SET actual=?,status='settled' WHERE cid=? AND key=?",
                (json.dumps(actual), cid, key),
            )

    def usage(self, cid):
        with self.transaction() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM usage WHERE cid=?", (cid,))
            ]
