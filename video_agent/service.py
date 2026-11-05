"""Application commands. The frontend never resumes a graph or writes approvals itself."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import threading
import uuid
import requests
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from .config import Settings, ROOT, LEGACY_DIR, LEGACY_ID
from .campaigns import build_brief, prepare_images, store_images, template_plan
from .storage import (
    Repository,
    read_json,
    save_json,
    signature,
    reset_approval,
    review_content,
)
from .control import DEFAULT_LIMITS
from .timeline import ad_key, revise_timeline
from .usage import limits_for, estimate, rates_for
from .workflow import CampaignRuntime, CampaignState
from .jobs import JobManager
from .connections import check_provider, redact, PROVIDERS


class CampaignService:
    def __init__(self, settings=None, secrets=None, import_notebook=False):
        self.settings, self.secrets = settings or Settings(), secrets or {}
        self.repository = Repository(self.settings)
        self.jobs, self.runtimes, self.lock = (
            JobManager(
                self.repository,
                scrub=lambda value: redact(value, self.settings.keys(self.secrets)),
            ),
            {},
            threading.RLock(),
        )
        if import_notebook:
            self.import_existing()

    def list_campaigns(self):
        """Expose user campaigns without resurfacing the old notebook example."""
        return [
            project
            for project in self.repository.list()
            if project["campaign_id"] != LEGACY_ID
            and not project.get("example", False)
            and not project.get("ui_hidden", False)
        ]

    def runtime(self, cid):
        with self.lock:
            if cid not in self.runtimes:
                self.runtimes[cid] = CampaignRuntime(
                    cid,
                    self.repository,
                    lambda message: self.jobs.note(
                        cid, redact(message, self.settings.keys(self.secrets))
                    ),
                    self.settings.keys(self.secrets),
                )
            return self.runtimes[cid]

    def snapshot(self, cid):
        runtime = self.runtime(cid)
        snapshot = runtime.snapshot()
        state = dict(snapshot.values)
        try:
            self.repository.require_approval(state)
            approved = True
        except (ValueError, KeyError, OSError):
            approved = False
        stage = snapshot.next[0] if snapshot.next else ""
        review_id = ""
        if state.get("plan") and snapshot.interrupts and not self.jobs.busy(cid):
            actions = {
                "approve_plan": ["approve", "revise_plan", "reopen_plan", "cancel"],
                "ready_sample": ["generate", "revise_plan", "reopen_plan", "cancel"],
                "approve_sample": [
                    "approve",
                    "revise",
                    "revise_plan",
                    "reopen_plan",
                    "cancel",
                ],
                "approve_final": [
                    "approve",
                    "revise",
                    "revise_plan",
                    "reopen_plan",
                    "cancel",
                ],
                "prepare_assets": ["continue", "cancel"],
            }.get(stage, [])
            ads = state["plan"]["ads"]
            scoped_ads = (
                ads[:1]
                if stage in ["approve_plan", "ready_sample"]
                else ads[1:]
                if stage in ["approve_sample", "prepare_assets"]
                else ads
            )
            scope = dict(
                plan=signature(state),
                ads=[ad_key(ad, state["brief"]) for ad in scoped_ads],
                limits=state["brief"].get("limits", DEFAULT_LIMITS),
                outputs=review_content(state, stage)["outputs"],
            )
            review_id = self.repository.control.issue(
                cid,
                stage,
                snapshot.config["configurable"]["checkpoint_id"],
                review_content(state, stage),
                actions,
                scope,
            )
        return dict(
            state=state,
            stage=stage,
            interrupted=bool(snapshot.interrupts),
            approved=approved,
            busy=self.jobs.busy(cid),
            signature=review_id,
            review_id=review_id,
            checkpoint=snapshot.config["configurable"].get("checkpoint_id", "")
            if snapshot.config
            else "",
            plan_signature=signature(state) if state.get("plan") else "",
            tasks=self.repository.control.tasks(cid),
            usage=self.repository.control.usage(cid),
            estimate=estimate(
                state["brief"],
                state["plan"]["ads"][:1]
                if stage in ["approve_plan", "ready_sample"]
                else state["plan"]["ads"][1:],
            )
            if state.get("plan")
            else {},
            missing_assets=self.missing_assets(
                cid,
                state["plan"]["ads"][:1]
                if stage in ["approve_plan", "ready_sample"]
                else state["plan"]["ads"],
            )
            if state.get("plan")
            else [],
            job=self.jobs.get(cid),
        )

    def missing_assets(self, cid, ads):
        runtime = self.runtime(cid)
        footage = (
            read_json(runtime.renderer.commute_config)
            if runtime.renderer.commute_config.exists()
            else {}
        )
        required = {
            key
            for ad in ads
            for scene in ad["scenes"]
            if scene.get("edit", {}).get("mode", "reused") in ["reused", "hybrid"]
            for key in scene["asset_ids"]
        }
        return sorted(
            key
            for key in required
            if not Path(
                footage.get(key)
                or runtime.renderer.assets.get(key, {}).get("path", "__missing__")
            ).is_file()
        )

    def create(
        self,
        name,
        audience,
        goal,
        starting_point="template",
        voice_backend="local_kokoro_notebook",
        voice_id="",
        refresh_research=False,
        *,
        product="",
        brand="",
        variant="",
        claims=None,
        cta="",
        style="Energetic, clear and realistic",
        competitors=None,
        product_url="",
        accent_color="#00AABD",
        use_footage=False,
        product_image=None,
        detail_image=None,
        style_images=(),
        limits=None,
        model="gemini-2.5-flash",
        allow_incomplete=True,
        usage_rates=None,
    ):
        if not name.strip() or not audience.strip() or not goal.strip():
            raise ValueError("Add a campaign name, audience and goal.")
        if not re.fullmatch(r"gemini-[A-Za-z0-9._-]+", model):
            raise ValueError("Use a valid Gemini planning model ID.")
        usage_rates = rates_for(usage_rates)
        if usage_rates and (
            set(usage_rates) - {"model_tokens", "speech_characters", "generation_jobs"}
            or any(
                not isinstance(v, (int, float)) or not 0 <= v <= 1_000_000_000
                for v in usage_rates.values()
            )
        ):
            raise ValueError(
                "Use nonnegative finite unit prices in micro USD per unit."
            )
        if voice_backend not in ["local_kokoro_notebook", "elevenlabs"]:
            raise ValueError("Unknown voice provider.")
        if voice_backend == "elevenlabs" and (
            not voice_id.strip()
            or not self.settings.keys(self.secrets).get("ELEVENLABS_API_KEY")
        ):
            raise ValueError(
                "ElevenLabs requires an API key and a voice ID. Local Kokoro needs no key."
            )
        if voice_backend == "elevenlabs" and not re.fullmatch(
            r"[A-Za-z0-9_-]{1,100}", voice_id.strip()
        ):
            raise ValueError(
                "Enter the voice ID itself, without a URL or query parameters."
            )
        if starting_point not in ["template", "ai"]:
            raise ValueError("Choose a local template or AI planning.")
        brief = build_brief(
            product,
            audience,
            goal,
            brand=brand,
            variant=variant,
            claims=claims,
            cta=cta,
            style=style,
            competitors=competitors,
            product_url=product_url,
            accent_color=accent_color,
            use_footage=use_footage,
            voice_backend=voice_backend,
            voice_id=voice_id.strip(),
            refresh_research=bool(refresh_research),
            limits=limits_for(limits),
            planning_model=model,
            usage_rates=usage_rates or {},
            voice_model=self.settings.keys(self.secrets).get("ELEVENLABS_MODEL")
            or "eleven_multilingual_v2"
            if voice_backend == "elevenlabs"
            else "kokoro-v1.0-int8",
        )
        prepared = prepare_images(
            product_image, detail_image, style_images, allow_incomplete=allow_incomplete
        )
        brief["asset_hashes"] = {a["asset_id"]: a["sha256"] for a, _ in prepared}
        brief["asset_ids"] = list(
            dict.fromkeys(["product_main", "product_detail", *brief["asset_hashes"]])
        )
        plan = template_plan(brief)
        from .models import CampaignPlan
        from .validation import plan_copy_issues

        issues = plan_copy_issues(CampaignPlan.model_validate(plan), brief)
        if issues:
            raise ValueError("The brief needs changes: " + "; ".join(issues[:3]))
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "campaign"
        cid = f"{slug}-{uuid.uuid4().hex[:8]}"
        store_images(self.repository.folder(cid) / "assets", prepared)
        self.repository.register(
            cid, name.strip(), product=brief["product"], imported=False
        )
        save_json(self.repository.folder(cid) / "brief.json", brief)
        runtime = self.runtime(cid)
        seed = dict(campaign_id=cid, brief=brief, research=[], version=0)
        if starting_point == "template":
            runtime.validate_plan(plan, brief)
            seed.update(
                plan=plan,
                version=1,
                planner="Local product template — no API call",
                status="awaiting_plan_approval",
            )

            def load_reference():
                if refresh_research:
                    seed["research"] += runtime.planner.research_competitors(
                        brief["competitors"]
                    )
                runtime.agent.update_state(runtime.config, seed, as_node="plan")
                runtime.agent.invoke(None, runtime.config)

            if refresh_research:
                self.jobs.submit(cid, "Refreshing campaign references", load_reference)
            else:
                load_reference()
        else:
            self.jobs.submit(
                cid,
                "Generating campaign concepts",
                lambda: runtime.agent.invoke(seed, runtime.config),
            )
        return cid

    def import_existing(self):
        """Read the existing checkpoint once; never modify the notebook's database."""
        if self.repository.folder(LEGACY_ID).joinpath("project.json").exists():
            return
        db = ROOT / "data" / "video-demo" / "checkpoints.sqlite"
        if not db.exists() or not (LEGACY_DIR / "plan.json").exists():
            return
        with SqliteSaver.from_conn_string(str(db)) as source:
            saved = source.get_tuple({"configurable": {"thread_id": LEGACY_ID}})
        if not saved:
            return
        state = {
            k: v
            for k, v in saved.checkpoint["channel_values"].items()
            if k in CampaignState.__annotations__
        }
        if not state.get("plan"):
            return
        runtime = self.runtime(LEGACY_ID)
        runtime.validate_plan(state["plan"], state["brief"])
        old_approval = (
            LEGACY_DIR / "approved_plans" / self.repository.approved_path(state).name
        )
        is_approved = False
        if old_approval.exists():
            record = read_json(old_approval)
            is_approved = record.get("approved") is True and record.get(
                "signature"
            ) == signature(state)
        if is_approved:
            state["approved_plan_path"] = self.repository.approve(
                state, "Imported earlier user approval from notebook"
            )
        status = state.get("status", "")
        if status == "complete" and state.get("outputs"):
            # Create a delivery bundle under the app only when final approval already exists.
            node = "export" if is_approved else "plan"
        elif is_approved and state.get("outputs"):
            node = "batch"
        elif is_approved and state.get("sample"):
            node = "sample"
        else:
            state.update(reset_approval(), status="awaiting_plan_approval")
            node = "plan"
        if node == "export":
            state.update(runtime.export(state))
        runtime.agent.update_state(runtime.config, state, as_node=node)
        runtime.renderer.brief = state["brief"]
        if node != "export":
            runtime.agent.invoke(None, runtime.config)
        self.repository.register(
            LEGACY_ID,
            "boAt Airdopes 141 · Black",
            product="boAt Airdopes 141",
            imported=True,
        )
        old_footage = LEGACY_DIR / "commute_inputs.json"
        if old_footage.exists():
            save_json(runtime.renderer.commute_config, read_json(old_footage))

    def update_brief(self, cid, changes, expected_signature):
        """Changing campaign facts regenerates a local draft and requires fresh approval."""
        with self.lock:
            snapshot = self.snapshot(cid)
            if snapshot["busy"]:
                raise ValueError("Wait for the current campaign job to finish.")
            if snapshot["signature"] != expected_signature:
                raise ValueError(
                    "This campaign changed. Refresh before editing the brief."
                )
            state = snapshot["state"]
            if state["brief"].get("schema_version", 1) < 2:
                raise ValueError(
                    "Create a new campaign to use different product assets. This preserves the imported example."
                )
            old = state["brief"]
            brief = build_brief(
                changes.get("product", old["product"]),
                changes.get("audience", old["audience"]),
                changes.get("goal", old["goal"]),
                **{
                    key: changes.get(key, old.get(key, default))
                    for key, default in {
                        "brand": "",
                        "variant": "",
                        "claims": list(old["approved_claims"].values()),
                        "cta": "",
                        "style": "Clear and realistic",
                        "competitors": [],
                        "product_url": "",
                        "accent_color": "#00AABD",
                        "use_footage": False,
                    }.items()
                },
                **{
                    key: old.get(key)
                    for key in ["voice_backend", "voice_id", "refresh_research"]
                },
            )
            brief.update(
                {
                    key: changes.get(key, old.get(key))
                    for key in [
                        "planning_model",
                        "voice_model",
                        "voice_speed",
                        "input_analysis",
                        "usage_rates",
                    ]
                    if old.get(key) is not None or key in changes
                }
            )
            brief["limits"] = limits_for(changes.get("limits", old.get("limits")))
            brief["usage_rates"] = rates_for(brief.get("usage_rates"))
            brief["asset_hashes"], brief["asset_ids"] = (
                old["asset_hashes"],
                old["asset_ids"],
            )
            plan = template_plan(brief)
            runtime = self.runtime(cid)
            runtime.validate_plan(plan, brief)
            self._replace_draft(
                cid, plan, brief, snapshot, "Local product template — edited brief"
            )
            save_json(self.repository.folder(cid) / "brief.json", brief)
            project_path = self.repository.folder(cid) / "project.json"
            project = read_json(project_path)
            save_json(project_path, dict(project, product=brief["product"]))
            self.jobs.resolve(
                cid, "Brief updated. Review and approve the new plan before rendering."
            )

    def act(self, cid, action, expected_signature="", feedback="", index=0):
        """Check stage, busy state and the exact displayed version before submitting work."""
        with self.lock:
            if self.jobs.busy(cid):
                raise ValueError("Wait for the current campaign job to finish.")
            runtime = self.runtime(cid)
            current = self.snapshot(cid)
            state, stage = current["state"], current["stage"]
            if state.get("plan") and expected_signature != current["signature"]:
                raise ValueError("The plan changed. Refresh and review it again.")
            keys = self.settings.keys(self.secrets)
            runtime.planner.keys = runtime.renderer.keys = keys
            if action == "retry":
                if current["interrupted"] or not stage:
                    raise ValueError(
                        "There is no interrupted processing step to retry."
                    )
                return self.jobs.submit(
                    cid,
                    "Retrying the interrupted step",
                    lambda: runtime.agent.invoke(None, runtime.config),
                )
            valid = [
                "approve_plan",
                "ready_sample",
                "approve_sample",
                "approve_final",
                "prepare_assets",
            ]
            if stage not in valid:
                raise ValueError("This campaign is not waiting for a review action.")
            if action == "revise_plan":
                if not feedback.strip():
                    raise ValueError("Describe the plan changes first.")
                if stage == "prepare_assets":
                    raise ValueError("Return to plan review before revising scripts.")

                def change_plan():
                    runtime.agent.update_state(
                        runtime.config,
                        dict(
                            reset_approval(state),
                            decision="revise",
                            feedback=feedback.strip(),
                            status="revising_plan",
                        ),
                        as_node="approve_plan",
                    )
                    runtime.agent.invoke(None, runtime.config)

                return self.jobs.submit(
                    cid,
                    "Revising campaign concepts",
                    change_plan,
                    authorization=lambda owner: self._authorize(
                        cid, current, action, owner
                    ),
                )
            if action == "reopen_plan":

                def reopen():
                    runtime.agent.update_state(
                        runtime.config,
                        dict(
                            reset_approval(state),
                            version=state["version"] + 1,
                            status="awaiting_plan_approval",
                        ),
                        as_node="plan",
                    )
                    runtime.agent.invoke(None, runtime.config)

                return self.jobs.submit(
                    cid,
                    "Opening plan review",
                    reopen,
                    authorization=lambda owner: self._authorize(
                        cid, current, action, owner
                    ),
                )
            if action == "generate" and stage != "ready_sample":
                raise ValueError(
                    "Approve and save the plan before generating a sample."
                )
            if action == "approve" and stage not in [
                "approve_plan",
                "approve_sample",
                "approve_final",
            ]:
                raise ValueError("There is no active approval at this step.")
            if action == "revise":
                if (
                    stage not in ["approve_sample", "approve_final"]
                    or not feedback.strip()
                ):
                    raise ValueError("Describe the requested video changes.")
                if index not in range(len(state["plan"]["ads"])):
                    raise ValueError("Choose an existing video.")
            if action == "continue" and stage != "prepare_assets":
                raise ValueError("No footage checkpoint is active.")
            if action not in ["approve", "generate", "revise", "cancel", "continue"]:
                raise ValueError("Unknown campaign action.")
            if action in ["generate", "approve"] and stage != "approve_plan":
                self.repository.require_approval(state)
            answer = dict(
                action=action,
                signature=current["signature"],
                feedback=feedback.strip(),
                index=index,
                review_id=current["review_id"],
                receipt=uuid.uuid4().hex,
                stage=stage,
            )
            approval_title = {
                "approve_plan": "Saving the approved plan",
                "approve_sample": "Rendering the remaining videos",
                "approve_final": "Preparing the final delivery bundle",
            }.get(stage, "Saving your approval")
            titles = {
                "approve": approval_title,
                "generate": "Rendering the representative sample",
                "revise": "Revising the selected script",
                "cancel": "Cancelling campaign",
                "continue": "Preparing remaining videos",
            }
            return self.jobs.submit(
                cid,
                titles[action],
                lambda: runtime.agent.invoke(Command(resume=answer), runtime.config),
                authorization=lambda owner: self._authorize(
                    cid, current, action, answer["receipt"]
                ),
            )

    def _authorize(self, cid, displayed, action, receipt):
        actual = self.runtime(cid).snapshot()
        state = dict(actual.values)
        stage = actual.next[0] if actual.next else ""
        checkpoint = actual.config["configurable"]["checkpoint_id"]
        self.repository.control.consume(
            displayed["review_id"],
            cid,
            stage,
            checkpoint,
            review_content(state, stage),
            action,
            receipt,
        )

    def _replace_draft(
        self,
        cid,
        plan,
        brief,
        displayed,
        label="Local revision",
        prepared_assets=None,
        lease_held=False,
    ):
        from contextlib import nullcontext

        runtime = self.runtime(cid)
        with nullcontext() if lease_held else self.repository.control.exclusive(cid):
            current = runtime.snapshot()
            if (
                current.config["configurable"]["checkpoint_id"]
                != displayed["checkpoint"]
            ):
                raise ValueError("The checkpoint changed. Refresh before editing.")
            self.repository.control.revoke(cid)
            if prepared_assets is not None:
                store_images(self.repository.folder(cid) / "assets", prepared_assets)
                assets = self.repository.assets(cid)
                runtime.planner.assets = runtime.renderer.assets = assets
            state = dict(current.values)
            prior = (
                state.get("prior_outputs", [])
                + state.get("outputs", [])
                + ([state["sample"]] if state.get("sample") else [])
            )
            unique = {item["video"]: item for item in prior}
            runtime.agent.update_state(
                runtime.config,
                dict(
                    reset_approval(),
                    brief=brief,
                    plan=plan,
                    prior_outputs=list(unique.values())[-50:],
                    version=state.get("version", 0) + 1,
                    status="awaiting_plan_approval",
                    planner=label,
                ),
                as_node="plan",
            )
            runtime.agent.invoke(None, runtime.config)
            save_json(self.repository.folder(cid) / "brief.json", brief)
        self.jobs.resolve(
            cid,
            "Revision saved. Fresh plan approval is required; unchanged media remains cached.",
        )

    def edit_timeline(self, cid, index, feedback, expected_signature):
        current = self.snapshot(cid)
        if current["busy"] or current["signature"] != expected_signature:
            raise ValueError("Wait for work to finish and refresh the current review.")
        plan = revise_timeline(current["state"]["plan"], index, feedback)
        self.save_manual_plan(cid, plan, expected_signature)

    def approve_pending_facts(self, cid, expected_signature):
        from .facts import normalized
        from .validation import plan_copy_issues
        from .models import CampaignPlan
        from .storage import digest, now

        current = self.snapshot(cid)
        if current["busy"] or current["signature"] != expected_signature:
            raise ValueError("The review changed; refresh before approving facts.")
        pending_path = self.repository.folder(cid) / "pending_facts.json"
        pending = read_json(pending_path)
        state = current["state"]
        if pending["brief_hash"] != digest(state["brief"]) or pending.get(
            "previous_plan_hash"
        ) != digest(state.get("plan")):
            raise ValueError("These proposed facts belong to an older draft.")
        plan, brief = pending["plan"], dict(state["brief"])
        issues = plan_copy_issues(CampaignPlan.model_validate(plan), brief)
        statements = list(
            dict.fromkeys(
                issue.split(":", 2)[-1].strip()
                for issue in issues
                if issue.startswith("Fact review required:")
            )
        )
        if not statements:
            raise ValueError("There are no new facts to approve.")
        facts = dict(brief.get("approved_claims", {}))
        mapping = {}
        for text in statements:
            key = "reviewed_" + digest(text)
            facts[key] = text
            mapping[normalized(text)] = key
        brief["approved_claims"] = facts
        for ad in plan["ads"]:
            for scene in ad["scenes"]:
                for field in ["headline", "caption", "narration", "footnote"]:
                    key = mapping.get(normalized(scene[field]))
                    if key and key not in scene["claim_ids"]:
                        scene["claim_ids"].append(key)
        self.runtime(cid).validate_plan(plan, brief)
        self._replace_draft(cid, plan, brief, current, "Explicitly reviewed new facts")
        save_json(
            self.repository.folder(cid)
            / "fact_approvals"
            / (uuid.uuid4().hex + ".json"),
            dict(
                statements=statements,
                approved_at=now(),
                source="Explicit human fact review",
                brief_hash=digest(brief),
            ),
        )
        pending_path.unlink(missing_ok=True)

    def refresh_research(self, cid, expected_signature):
        current = self.snapshot(cid)
        if current["signature"] != expected_signature:
            raise ValueError("Refresh the current review first.")

        def refresh():
            runtime = self.runtime(cid)
            sources = runtime.planner.research_competitors(
                current["state"]["brief"].get("competitors", []), refresh=True
            )
            self.repository.control.revoke(cid)
            runtime.agent.update_state(
                runtime.config,
                dict(
                    reset_approval(current["state"]),
                    research=sources,
                    version=current["state"]["version"] + 1,
                    status="awaiting_plan_approval",
                ),
                as_node="plan",
            )
            runtime.agent.invoke(None, runtime.config)

        return self.jobs.submit(cid, "Refreshing public competitor sources", refresh)

    def stop_work(self, cid):
        self.repository.control.cancel(cid)
        self.jobs.note(
            cid,
            "Cancellation requested. Queued/future tasks stop; work already submitted may finish. No global provider interrupt is sent.",
        )
        if self.settings.keys(self.secrets).get("COMFY_WORKFLOW"):
            from .generation import ComfyFootage

            try:
                ComfyFootage(
                    self.settings.keys(self.secrets), self.repository.control, cid
                ).cancel_queued()
            except Exception:
                self.jobs.note(
                    cid,
                    "Provider queue could not be confirmed. Reconcile recorded provider jobs; already submitted work may still finish.",
                )

    def resume_work(self, cid):
        if self.jobs.busy(cid):
            raise ValueError("Wait for the running task to stop first.")
        self.repository.control.cancel(cid, False)

    def reconcile_unknown(self, cid, key, not_submitted=False):
        from .storage import now
        from json import loads

        if self.jobs.busy(cid):
            raise ValueError(
                "Wait for the active worker to stop before reconciling its request."
            )
        with self.repository.control.exclusive(cid):
            task = self.repository.control.task(cid, key)
            if not task or task["status"] not in ["unknown", "submitting"]:
                raise ValueError(
                    "This task does not have an unknown submission outcome."
                )
            if task["kind"] == "generation":
                from .generation import ComfyFootage
                from .storage import digest

                try:
                    found = ComfyFootage(
                        self.settings.keys(self.secrets), self.repository.control, cid
                    ).reconcile(digest([cid, key.removeprefix("generation:")]))
                except requests.RequestException:
                    raise RuntimeError(
                        "The local provider is unreachable. Its outcome remains unknown; reconnect it before reconciliation."
                    ) from None
                if found:
                    self.repository.control.task_update(
                        cid, key, "running", provider_id=found
                    )
                    return "Provider job found. Retry processing to poll/download it without a new submission."
            for row in self.repository.control.usage(cid):
                if row["key"].startswith(key + ":") and row["status"] == "reserved":
                    actual = (
                        {name: 0 for name in loads(row["reserved"])}
                        if not_submitted
                        else loads(row["reserved"])
                    )
                    self.repository.control.settle(cid, row["key"], actual)
            self.repository.control.task_update(
                cid,
                key,
                "failed",
                error="Outcome explicitly reconciled by user; a bounded retry may proceed.",
            )
            save_json(
                self.repository.folder(cid)
                / "reconciliations"
                / (uuid.uuid4().hex + ".json"),
                dict(
                    task=key,
                    not_submitted=not_submitted,
                    checked_at=now(),
                    source="Explicit user provider/account check",
                ),
            )
            return "Outcome recorded. Existing allowance and retry limits still apply."

    def add_input(self, cid, name, content, kind, expected_signature):
        from .storage import digest
        from .inputs import InputAnalyzer

        current = self.snapshot(cid)
        if current["busy"] or current["signature"] != expected_signature:
            raise ValueError(
                "Wait for work to finish and refresh before adding inputs."
            )
        suffix = Path(name).suffix.lower()
        allowed = {
            "text": [".txt"],
            "image": [".png", ".jpg", ".jpeg", ".webp"],
            "audio": [".wav", ".mp3", ".m4a", ".ogg", ".webm"],
            "video": [".mp4", ".mov", ".webm", ".mkv"],
        }
        if (
            suffix not in allowed.get(kind, [])
            or not 0 < len(content) <= 200 * 1024 * 1024
        ):
            raise ValueError(
                "Unsupported input file or size; use a supported file up to 200 MB."
            )
        folder = self.repository.folder(cid) / "input"
        folder.mkdir(exist_ok=True)
        path = folder / (digest(list(content[:100])) + uuid.uuid4().hex[:8] + suffix)
        path.write_bytes(content)

        def analyze():
            runtime = self.runtime(cid)
            result = InputAnalyzer(
                self.repository.folder(cid) / "analysis",
                self.settings.keys(self.secrets),
            ).analyze(path, kind, runtime.renderer.probe)
            state = dict(runtime.snapshot().values)
            brief = dict(state["brief"])
            analyses = {
                item["file_hash"]: item for item in brief.get("input_analysis", [])
            }
            analyses[result["file_hash"]] = result
            brief["input_analysis"] = list(analyses.values())
            self.repository.control.revoke(cid)
            runtime.agent.update_state(
                runtime.config,
                dict(
                    reset_approval(state),
                    brief=brief,
                    plan=state.get("plan") or template_plan(brief),
                    version=state["version"] + 1,
                    status="awaiting_plan_approval",
                ),
                as_node="plan",
            )
            runtime.agent.invoke(None, runtime.config)
            save_json(self.repository.folder(cid) / "brief.json", brief)

        return self.jobs.submit(cid, "Analyzing reference input", analyze)

    def add_product_images(self, cid, main, detail, expected_signature):
        current = self.snapshot(cid)
        if current["busy"] or current["signature"] != expected_signature:
            raise ValueError("Refresh before replacing product images.")
        prepared = prepare_images(main, detail)
        state = current["state"]
        brief = dict(
            state["brief"],
            asset_hashes={a["asset_id"]: a["sha256"] for a, _ in prepared},
            asset_ids=[a["asset_id"] for a, _ in prepared],
        )
        # Content-addressed filenames preserve earlier asset versions.
        self._replace_draft(
            cid,
            state["plan"],
            brief,
            current,
            "Updated product identity images",
            prepared_assets=prepared,
        )
        assets = self.repository.assets(cid)
        self.runtime(cid).planner.assets = self.runtime(cid).renderer.assets = assets

    def use_saved_draft(self, cid, expected_signature):
        current = self.snapshot(cid)
        if current["busy"] or current["signature"] != expected_signature:
            raise ValueError("Wait for work to finish and refresh the draft.")
        if current["stage"] != "plan":
            raise ValueError(
                "A saved draft can replace only an interrupted planning step."
            )
        state = current["state"]
        plan = state.get("plan") or template_plan(state["brief"])
        self.runtime(cid).validate_plan(plan, state["brief"])
        self._replace_draft(
            cid, plan, state["brief"], current, "Saved/local draft ? no new AI call"
        )

    def save_manual_plan(self, cid, plan, expected_signature):
        from .storage import digest

        with self.lock:
            current = self.snapshot(cid)
            if current["busy"]:
                raise ValueError("Wait for the current job to finish.")
            if current["signature"] != expected_signature:
                raise ValueError("This draft has changed. Refresh first.")
            previous = current["state"].get("plan", {})
            for old, new in zip(previous.get("ads", []), plan.get("ads", [])):
                if old.get("music_direction") != new.get("music_direction") and old.get(
                    "audio"
                ) == new.get("audio"):
                    raise ValueError(
                        "Music notes do not execute edits. Change the supported audio timeline parameters."
                    )
                for before, after in zip(old["scenes"], new["scenes"]):
                    if (
                        before.get("visual_direction") != after.get("visual_direction")
                        and before.get("edit") == after.get("edit")
                        and before["asset_ids"] == after["asset_ids"]
                    ):
                        raise ValueError(
                            "Visual notes do not execute edits. Change the supported structured timeline."
                        )
            brief = current["state"]["brief"]
            if any(
                scene.get("edit", {}).get("mode", "reused") != "reused"
                for ad in plan["ads"]
                for scene in ad["scenes"]
            ):
                from .generation import ComfyFootage
                from .cache import file_hash

                keys = self.settings.keys(self.secrets)
                ready = ComfyFootage(keys, self.repository.control, cid).readiness()
                if not ready["available"]:
                    raise ValueError(
                        ready["message"] + " Select reused image-based mode instead."
                    )
                brief = dict(
                    brief,
                    generation=dict(
                        workflow_sha256=file_hash(keys["COMFY_WORKFLOW"]),
                        minimum_vram_gb=float(keys.get("COMFY_MIN_VRAM_GB") or 10),
                    ),
                )
            try:
                self.runtime(cid).validate_plan(plan, brief)
            except ValueError as error:
                if "Fact review required" in str(error):
                    save_json(
                        self.repository.folder(cid) / "pending_facts.json",
                        dict(
                            plan=plan,
                            brief_hash=digest(current["state"]["brief"]),
                            previous_plan_hash=digest(current["state"].get("plan")),
                            issues=[str(error)],
                        ),
                    )
                raise
            self._replace_draft(
                cid, plan, brief, current, "Manual script/timeline edit"
            )

    def save_footage(self, cid, name, content, kind="lifestyle_clip"):
        from .cache import file_hash

        with self.lock:
            current = self.snapshot(cid)
            if current["busy"]:
                raise ValueError(
                    "Wait for generation to finish before changing footage."
                )
            if kind not in [
                "commute_walk",
                "commute_transit",
                "lifestyle_clip",
                "lifestyle_detail",
            ]:
                raise ValueError("Unknown footage role.")
            suffix = Path(name).suffix.lower()
            if (
                suffix not in [".mp4", ".mov", ".mkv", ".webm"]
                or not 0 < len(content) <= 200 * 1024 * 1024
            ):
                raise ValueError("Upload a supported video up to 200 MB.")
            with self.repository.control.exclusive(cid):
                runtime = self.runtime(cid)
                if (
                    runtime.snapshot().config["configurable"]["checkpoint_id"]
                    != current["checkpoint"]
                ):
                    raise ValueError(
                        "The checkpoint changed; refresh before uploading."
                    )
                folder = self.repository.folder(cid) / "input"
                folder.mkdir(exist_ok=True)
                path = folder / (kind + "-" + uuid.uuid4().hex[:8] + suffix)
                path.write_bytes(content)
                try:
                    info = runtime.renderer.probe(path)
                    if not any(
                        stream["codec_type"] == "video" for stream in info["streams"]
                    ):
                        raise ValueError("The upload has no video stream.")
                except Exception:
                    path.unlink(missing_ok=True)
                    raise ValueError(
                        "The uploaded file is not a readable video."
                    ) from None
                files = (
                    read_json(runtime.renderer.commute_config)
                    if runtime.renderer.commute_config.exists()
                    else {}
                )
                files[kind] = str(path)
                save_json(runtime.renderer.commute_config, files)
                brief = dict(current["state"]["brief"])
                brief["asset_hashes"] = dict(
                    brief.get("asset_hashes", {}), **{kind: file_hash(path)}
                )
                brief["asset_ids"] = list(
                    dict.fromkeys([*brief.get("asset_ids", []), kind])
                )
                self._replace_draft(
                    cid,
                    current["state"]["plan"],
                    brief,
                    current,
                    "Updated planned footage ? fresh approval required",
                    lease_held=True,
                )
                return str(path)

    def check_connections(self, providers):
        if not providers or set(providers) - set(PROVIDERS):
            raise ValueError("Select valid providers to check.")

        def check():
            keys = self.settings.keys(self.secrets)
            path = self.settings.data_dir / "api_checks.json"
            results = read_json(path) if path.exists() else {}
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [
                    pool.submit(check_provider, service, keys) for service in providers
                ]
                for future in as_completed(futures):
                    result = future.result()
                    results[result["service"]] = result
                    save_json(path, results)
                    self.jobs.note(
                        "api-diagnostics",
                        f"{result['service']}: HTTP {result['status']}",
                    )

        return self.jobs.submit("api-diagnostics", "Checking API connections", check)

    def close(self):
        self.jobs.close()
        for runtime in self.runtimes.values():
            runtime.close()
