"""Durable LangGraph checkpoints; every media path requires a matching approval."""

from pathlib import Path
import sqlite3
import zipfile
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt
from .models import CampaignPlan
from .validation import plan_copy_issues
from .storage import read_json, save_json, signature, reset_approval
from .planning import Planner
from .rendering.renderer import VideoRenderer


class CampaignState(TypedDict, total=False):
    campaign_id: str
    brief: dict
    research: list
    plan: dict
    planner: str
    version: int
    decision: str
    feedback: str
    revision_index: int
    sample: dict
    outputs: list
    status: str
    approved_plan_path: str
    export_path: str


class CampaignRuntime:
    def __init__(self, cid, repository, note, keys):
        self.cid, self.repository, self.note = cid, repository, note
        folder = repository.folder(cid)
        folder.mkdir(parents=True, exist_ok=True)
        assets = repository.assets(cid)
        self.planner = Planner(folder, keys, assets, note)
        self.renderer = VideoRenderer(folder, repository.settings, assets, keys, note)
        brief_path = folder / "brief.json"
        self.renderer.brief = read_json(brief_path) if brief_path.exists() else {}
        self.connection = sqlite3.connect(
            repository.settings.data_dir / "checkpoints.sqlite", check_same_thread=False
        )
        self.config = {"configurable": {"thread_id": cid}}
        self.builder = self.build_graph()
        self.agent = self.builder.compile(checkpointer=SqliteSaver(self.connection))
        self.renderer.brief = self.snapshot().values.get("brief", self.renderer.brief)

    def build_graph(self):
        builder = StateGraph(CampaignState)
        nodes = {
            "understand": self.understand,
            "research": self.research,
            "plan": self.plan,
            "approve_plan": self.approve_plan,
            "save_approved_plan": self.save_approved_plan,
            "ready_sample": self.ready_sample,
            "sample": self.sample,
            "approve_sample": self.approve_sample,
            "revise_sample": self.revise_sample,
            "prepare_assets": self.prepare_assets,
            "batch": self.batch,
            "approve_final": self.approve_final,
            "revise_selected": self.revise_selected,
            "export": self.export,
        }
        for name, function in nodes.items():
            builder.add_node(name, function)
        for a, b in [
            (START, "understand"),
            ("understand", "research"),
            ("research", "plan"),
            ("plan", "approve_plan"),
            ("save_approved_plan", "ready_sample"),
            ("sample", "approve_sample"),
            ("revise_sample", "approve_plan"),
            ("batch", "approve_final"),
            ("revise_selected", "approve_plan"),
            ("export", END),
        ]:
            builder.add_edge(a, b)
        routes = {
            "approve_plan": {
                "approve": "save_approved_plan",
                "revise": "plan",
                "cancel": END,
            },
            "ready_sample": {"generate": "sample", "revise": "plan", "cancel": END},
            "approve_sample": {
                "approve": "prepare_assets",
                "revise": "revise_sample",
                "cancel": END,
            },
            "approve_final": {
                "approve": "export",
                "revise": "revise_selected",
                "cancel": END,
            },
        }
        for node, route in routes.items():
            builder.add_conditional_edges(node, lambda s: s["decision"], route)
        builder.add_conditional_edges(
            "prepare_assets",
            lambda s: "cancel" if s.get("status") == "cancelled" else "ready",
            {"cancel": END, "ready": "batch"},
        )
        return builder

    def snapshot(self):
        return self.agent.get_state(self.config)

    def understand(self, state):
        brief = state["brief"]
        if brief.get("count") != 3 or not brief.get("product"):
            raise ValueError("Add a product name for this three-video campaign.")
        if brief.get("voice_backend") == "elevenlabs" and not brief.get("voice_id"):
            raise ValueError("Choose a voice ID before using ElevenLabs.")
        self.note("Campaign brief validated.")
        return {"status": "planning"}

    def research(self, state):
        sources = state.get("research", [])
        if state["brief"].get("refresh_research"):
            sources = sources + self.planner.research_competitors(
                state["brief"]["competitors"]
            )
        return {"research": sources}

    def plan(self, state):
        plan, provider = self.planner.make_plan(
            state["brief"],
            state.get("research", []),
            state.get("feedback", ""),
            state.get("plan"),
        )
        self.validate_plan(plan, state["brief"])
        return dict(
            reset_approval(),
            plan=plan,
            planner=provider,
            version=state.get("version", 0) + 1,
            status="awaiting_plan_approval",
        )

    def validate_plan(self, plan, brief):
        parsed = CampaignPlan.model_validate(plan)
        issues = plan_copy_issues(parsed, brief)
        if issues:
            raise ValueError("Plan needs changes: " + "; ".join(issues[:5]))

    def review(self, state, stage, actions=("approve", "revise", "cancel")):
        answer = interrupt(
            {
                "stage": stage,
                "signature": signature(state),
                "version": state["version"],
                "actions": actions,
            }
        )
        if answer.get("action") not in actions:
            raise ValueError("Invalid review action.")
        if answer["action"] in ["approve", "generate"] and answer.get(
            "signature"
        ) != signature(state):
            raise ValueError(
                "The plan changed. Refresh and review the current version."
            )
        if answer["action"] == "revise" and not answer.get("feedback", "").strip():
            raise ValueError("Describe the requested changes.")
        index = int(answer.get("index", 0))
        if index not in range(len(state["plan"]["ads"])):
            raise ValueError("Choose an existing video.")
        if answer["action"] in ["approve", "generate"] and stage != "plan":
            self.repository.require_approval(state)
        self.note(f"{stage}: {answer['action']}")
        return dict(
            decision=answer["action"],
            feedback=answer.get("feedback", ""),
            revision_index=index,
            status="cancelled" if answer["action"] == "cancel" else state["status"],
        )

    def approve_plan(self, state):
        return self.review(state, "plan")

    def approve_sample(self, state):
        return self.review(state, "sample")

    def approve_final(self, state):
        return self.review(state, "final")

    def save_approved_plan(self, state):
        if state.get("decision") != "approve":
            raise ValueError("Explicit plan approval is required.")
        self.validate_plan(state["plan"], state["brief"])
        path = self.repository.approve(state)
        self.note("Approved plan and scripts saved.")
        return dict(approved_plan_path=path, status="ready_to_generate_sample")

    def ready_sample(self, state):
        # The review helper rechecks approval for generation, while still allowing
        # cancellation or revision if an approval file is missing or damaged.
        return self.review(state, "generation", ("generate", "revise", "cancel"))

    def sample(self, state):
        self.repository.require_approval(state)
        return dict(
            sample=self.renderer.render(
                state, state["plan"]["ads"][0], self.repository
            ),
            status="awaiting_sample_approval",
        )

    def revise_sample(self, state):
        return self.revise(state, 0)

    def revise_selected(self, state):
        return self.revise(state, state["revision_index"])

    def revise(self, state, index):
        plan = self.planner.revise_ad(state, index, state["feedback"])
        self.validate_plan(plan, state["brief"])
        return dict(
            reset_approval(),
            plan=plan,
            version=state["version"] + 1,
            status="awaiting_plan_approval",
        )

    def prepare_assets(self, state):
        self.repository.require_approval(state)
        files = self.renderer.commute_config
        required = {
            key
            for ad in state["plan"]["ads"]
            for scene in ad["scenes"]
            for key in scene["asset_ids"]
            if key.startswith(("commute_", "lifestyle_"))
        }
        if state["brief"].get("schema_version", 1) < 2:
            required.discard(
                "commute_transit"
            )  # Optional in the preserved notebook campaign.

        def missing():
            saved = read_json(files) if files.exists() else {}
            return sorted(
                key
                for key in required
                if not saved.get(key) or not Path(saved[key]).is_file()
            )

        if missing():
            answer = interrupt(
                {
                    "stage": "footage",
                    "message": "Upload the planned lifestyle footage.",
                    "missing": missing(),
                }
            )
            if answer.get("action") == "cancel":
                return {"status": "cancelled"}
            if missing():
                raise ValueError(
                    "Required footage is still missing: " + ", ".join(missing())
                )
        return {"status": "rendering_remaining"}

    def batch(self, state):
        self.repository.require_approval(state)
        outputs = [state["sample"]]
        for number, ad in enumerate(state["plan"]["ads"][1:], 2):
            self.note(f"Rendering video {number}/3: {ad['title']}")
            outputs.append(self.renderer.render(state, ad, self.repository))
        return dict(outputs=outputs, status="awaiting_final_approval")

    def export(self, state):
        self.repository.require_approval(state)
        folder = (
            self.repository.folder(self.cid)
            / "deliverables"
            / f"v{state['version']:03d}"
        )
        folder.mkdir(parents=True, exist_ok=True)
        manifest = folder / "campaign.json"
        save_json(
            manifest,
            {k: state[k] for k in ["brief", "plan", "research", "outputs", "version"]},
        )
        with zipfile.ZipFile(
            folder / "campaign.zip", "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.write(manifest, "campaign.json")
            archive.write(self.repository.approved_path(state), "approved_plan.json")
            for index, result in enumerate(state["outputs"], 1):
                for key, suffix in [("video", ".mp4"), ("captions", ".srt")]:
                    archive.write(
                        result[key],
                        f"{index:02d}_{state['plan']['ads'][index - 1]['video_type']}{suffix}",
                    )
        self.note("Final approval saved. Delivery bundle ready.")
        return dict(status="complete", export_path=str(folder / "campaign.zip"))

    def close(self):
        self.connection.close()
