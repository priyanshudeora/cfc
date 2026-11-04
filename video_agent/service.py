"""Application commands. The frontend never resumes a graph or writes approvals itself."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import threading
import uuid
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from .config import Settings, ROOT, LEGACY_DIR, LEGACY_ID
from .campaigns import build_brief, prepare_images, store_images, template_plan
from .storage import Repository, read_json, save_json, signature, reset_approval
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
        return dict(
            state=state,
            stage=snapshot.next[0] if snapshot.next else "",
            interrupted=bool(snapshot.interrupts),
            approved=approved,
            busy=self.jobs.busy(cid),
            signature=signature(state) if state.get("plan") else "",
            job=self.jobs.get(cid),
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
    ):
        if not name.strip() or not audience.strip() or not goal.strip():
            raise ValueError("Add a campaign name, audience and goal.")
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
        )
        prepared = prepare_images(product_image, detail_image, style_images)
        brief["asset_hashes"] = {a["asset_id"]: a["sha256"] for a, _ in prepared}
        brief["asset_ids"] = list(brief["asset_hashes"])
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
            brief["asset_hashes"], brief["asset_ids"] = (
                old["asset_hashes"],
                old["asset_ids"],
            )
            plan = template_plan(brief)
            runtime = self.runtime(cid)
            runtime.validate_plan(plan, brief)
            runtime.agent.update_state(
                runtime.config,
                dict(
                    reset_approval(),
                    brief=brief,
                    plan=plan,
                    research=[],
                    version=state["version"] + 1,
                    planner="Local product template — edited brief",
                    status="awaiting_plan_approval",
                ),
                as_node="plan",
            )
            runtime.agent.invoke(None, runtime.config)
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
                            reset_approval(),
                            decision="revise",
                            feedback=feedback.strip(),
                            status="revising_plan",
                        ),
                        as_node="approve_plan",
                    )
                    runtime.agent.invoke(None, runtime.config)

                return self.jobs.submit(cid, "Revising campaign concepts", change_plan)
            if action == "reopen_plan":

                def reopen():
                    runtime.agent.update_state(
                        runtime.config,
                        dict(
                            reset_approval(),
                            version=state["version"] + 1,
                            status="awaiting_plan_approval",
                        ),
                        as_node="plan",
                    )
                    runtime.agent.invoke(None, runtime.config)

                return self.jobs.submit(cid, "Opening plan review", reopen)
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
            )

    def use_saved_draft(self, cid, expected_signature):
        with self.lock:
            snapshot = self.snapshot(cid)
            if snapshot["busy"]:
                raise ValueError("Wait for the current job to finish.")
            if snapshot["signature"] != expected_signature:
                raise ValueError("The draft changed. Refresh first.")
            if snapshot["stage"] != "plan":
                raise ValueError(
                    "A saved draft can replace only an interrupted planning step."
                )
            runtime, state = self.runtime(cid), snapshot["state"]
            plan = state.get("plan") or template_plan(state["brief"])
            runtime.validate_plan(plan, state["brief"])
            runtime.agent.update_state(
                runtime.config,
                dict(
                    reset_approval(),
                    plan=plan,
                    version=state.get("version", 0) + 1,
                    planner="Saved draft — no new AI call",
                    status="awaiting_plan_approval",
                ),
                as_node="plan",
            )
            runtime.agent.invoke(None, runtime.config)

            self.jobs.resolve(
                cid, "Recovered with the saved draft; plan approval is required."
            )

    def save_manual_plan(self, cid, plan, expected_signature):
        with self.lock:
            snapshot = self.snapshot(cid)
            if snapshot["busy"]:
                raise ValueError("Wait for the current job to finish.")
            if snapshot["signature"] != expected_signature:
                raise ValueError("This draft has changed. Refresh first.")
            runtime, state = self.runtime(cid), snapshot["state"]
            runtime.validate_plan(plan, state["brief"])
            runtime.agent.update_state(
                runtime.config,
                dict(
                    reset_approval(),
                    plan=plan,
                    version=state["version"] + 1,
                    status="awaiting_plan_approval",
                    planner="Manual script edit",
                ),
                as_node="plan",
            )
            runtime.agent.invoke(None, runtime.config)
            self.jobs.resolve(
                cid, "A local revision is saved and awaiting plan approval."
            )

    def save_footage(self, cid, name, content, kind="lifestyle_clip"):
        with self.lock:
            if self.jobs.busy(cid):
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
                raise ValueError("Upload an MP4, MOV, MKV or WebM video up to 200 MB.")
            runtime = self.runtime(cid)
            folder = self.repository.folder(cid) / "input"
            folder.mkdir(exist_ok=True)
            path = folder / (kind + "-" + uuid.uuid4().hex[:8] + suffix)
            path.write_bytes(content)
            try:
                probe = runtime.renderer.probe(path)
                if not any(s.get("codec_type") == "video" for s in probe["streams"]):
                    raise ValueError("The uploaded file has no readable video stream.")
            except Exception:
                path.unlink(missing_ok=True)
                raise ValueError(
                    "This upload is not a readable video. Try another clip."
                ) from None
            files = (
                read_json(runtime.renderer.commute_config)
                if runtime.renderer.commute_config.exists()
                else {}
            )
            files[kind] = str(path)
            save_json(runtime.renderer.commute_config, files)
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
