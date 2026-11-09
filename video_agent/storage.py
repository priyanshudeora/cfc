"""Atomic files, versioned approvals and campaign metadata."""

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import uuid
import time


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value):
    return authorization_digest(value)[:32]


def authorization_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                # Windows sync/indexing tools can briefly hold the destination.
                # Keep the previous JSON intact, retry only known Windows codes,
                # and surface permanent permissions failures after a bounded wait.
                if getattr(error, "winerror", None) not in [5, 32, 33] or attempt == 7:
                    raise
                time.sleep(min(0.05 * 2**attempt, 0.5))
    finally:
        temporary.unlink(missing_ok=True)


class Repository:
    def __init__(self, settings):
        self.settings = settings
        settings.campaigns_dir.mkdir(parents=True, exist_ok=True)
        from .control import ControlStore

        self.control = ControlStore(settings.data_dir / "control.sqlite")

    def folder(self, campaign_id):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", campaign_id):
            raise ValueError("Invalid campaign identifier.")
        return self.settings.campaigns_dir / campaign_id

    def register(self, campaign_id, name, **extra):
        data = dict(campaign_id=campaign_id, name=name, created_at=now(), **extra)
        save_json(self.folder(campaign_id) / "project.json", data)
        return data

    def list(self):
        return sorted(
            (read_json(p) for p in self.settings.campaigns_dir.glob("*/project.json")),
            key=lambda d: d.get("created_at", ""),
            reverse=True,
        )

    def assets(self, campaign_id):
        manifest = self.folder(campaign_id) / "assets" / "asset_manifest.json"
        if manifest.exists():
            base = manifest.parent
        else:
            from .config import LEGACY_ID

            if campaign_id != LEGACY_ID:
                return {}
            base = self.settings.reference_dir
            manifest = base / "asset_manifest.json"
        entries = read_json(manifest)["assets"]
        assets = {
            item["asset_id"]: dict(item, path=str(base / item["file"]))
            for item in entries
        }
        for key, asset in assets.items():
            p = Path(asset["path"])
            if (
                not p.is_file()
                or hashlib.sha256(p.read_bytes()).hexdigest() != asset["sha256"]
            ):
                raise ValueError(
                    "The supplied original product image is missing or changed: " + key
                )
        return assets

    def approved_path(self, state):
        return (
            self.folder(state["campaign_id"])
            / "approved_plans"
            / f"plan_v{state['version']:03d}_{signature(state)}.json"
        )

    def approve(self, state, source="Streamlit: Approve plan"):
        from .documents import plan_markdown

        path = self.approved_path(state)
        record = dict(
            approved=True,
            signature=signature(state),
            approved_at=now(),
            approval_source=source,
            version=state["version"],
            campaign_id=state["campaign_id"],
            brief=state["brief"],
            plan=state["plan"],
            research=state.get("research", []),
        )
        # Write the readable plan before publishing the approval record.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".md").write_text(
            plan_markdown(state["plan"]), encoding="utf-8"
        )
        save_json(path, record)
        save_json(path.parent / "latest_approved_plan.json", record)
        return str(path)

    def require_approval(self, state):
        path = self.approved_path(state)
        if not path.is_file():
            raise ValueError(
                "Review and approve this plan version before generating media."
            )
        record = read_json(path)
        if (
            record.get("approved") is not True
            or record.get("signature") != signature(state)
            or record.get("plan") != state["plan"]
            or record.get("brief") != state["brief"]
        ):
            raise ValueError("This plan differs from its approval. Review it again.")
        return record


def signature(state):
    # Plan identity is deliberately separate from a single-use review identifier.
    return authorization_digest(
        [state["campaign_id"], state["brief"], state["plan"], state["version"]]
    )


def artifact_identity(result):
    if not result:
        return {}
    files = {}
    roles = ["video", "captions", "voice"] + (
        ["source_manifest"] if result.get("source_manifest") else []
    )
    for role in roles:
        path = Path(result.get(role, ""))
        files[role] = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else "missing"
        )
    return dict(
        files=files,
        version=result.get("version"),
        paths={role: result.get(role, "") for role in roles},
        content_key=result.get("content_key"),
        quality=result.get("quality"),
    )


def review_content(state, stage):
    return dict(
        plan=signature(state),
        sample=artifact_identity(state.get("sample"))
        if stage in ["approve_sample", "prepare_assets"]
        else {},
        outputs=[artifact_identity(item) for item in state.get("outputs", [])]
        if stage == "approve_final"
        else [],
    )


def reset_approval(state=None):
    result = dict(approved_plan_path="", sample={}, outputs=[], export_path="")
    if state:
        prior = (
            state.get("prior_outputs", [])
            + state.get("outputs", [])
            + ([state["sample"]] if state.get("sample") else [])
        )
        result["prior_outputs"] = list(
            {item["video"]: item for item in prior}.values()
        )[-50:]
    return result
