"""Shared native Streamlit controls; all business actions live in CampaignService."""

from pathlib import Path
import streamlit as st
from .config import Settings

STAGE_LABELS = {
    "approve_plan": (
        "Plan review",
        "Review the three concepts and scripts, then approve this version or request changes.",
    ),
    "ready_sample": (
        "Ready for a sample",
        "Your approved plan is saved. Generate one representative sample when ready.",
    ),
    "approve_sample": (
        "Sample approval needed",
        "Review the visuals, voice, music and captions. Your approval unlocks the remaining two videos.",
    ),
    "prepare_assets": (
        "Lifestyle footage needed",
        "Upload the planned licensed footage before the story video can be rendered.",
    ),
    "approve_final": (
        "Final review",
        "All three previews are ready. Approve the final set to prepare the delivery bundle.",
    ),
}
FORMAT_NAMES = {
    "product_demo": "Product demonstration",
    "student_commute": "Student commute",
    "lifestyle_story": "Lifestyle story",
    "product_reveal": "Product reveal",
}


def generated_clip_panel(sources):
    """Raw model clips and original job IDs; never initiate generation here."""
    generated = [
        (i, s)
        for i, s in enumerate(sources, 1)
        if s.get("source") in ["generated footage", "compositing", "planned AI footage"]
    ]
    if not generated:
        st.caption(
            (
                "Supplied media / image animation only. "
                if sources
                else "No generated-clip provenance is recorded for this preview. "
            )
            + "AI concepts writes scripts; Creation controls the footage source."
        )
        return
    st.markdown("**Generated clips**")
    st.caption(
        "Raw model output before FFmpeg adds layout, narration, music and captions. Cached clips retain their original ComfyUI job IDs."
    )
    for i, source in generated:
        label = f"Generated clip · Scene {source.get('scene_index', i - 1) + 1}"
        if source.get("title"):
            label += " · " + source["title"]
        with st.expander(label):
            identifier = source.get("provider_id", "")
            st.caption("ComfyUI job ID: " + (identifier or "Not submitted"))
            st.caption(
                "Workflow: "
                + (
                    source.get("workflow_name")
                    or source.get("workflow_sha256")
                    or "See the saved campaign profile"
                )
            )
            if source.get("models"):
                st.caption("Models: " + ", ".join(source["models"]))
            if source.get("workflow_sha256"):
                st.caption("Workflow SHA-256: " + source["workflow_sha256"])
            path = Path(source.get("path") or "__missing__")
            if path.is_file():
                st.video(
                    str(path),
                    format="video/webm"
                    if path.suffix.lower() == ".webm"
                    else "video/mp4",
                    alt=label + ", raw model footage",
                )
                st.caption(
                    f"Native clip: {source.get('settings', {}).get('frames', '?')} frames at {source.get('settings', {}).get('fps', '?')} fps. Review product identity; generated footage can distort it."
                )
            elif source.get("error"):
                st.error(source["error"])
            else:
                st.info(
                    "Raw clip is "
                    + source.get("status", "missing")
                    + ". Use Retry after resolving the error; completed clips remain cached."
                )


def workflow_step(snapshot):
    """Present persisted state without treating page navigation as progress."""
    state, stage = snapshot.get("state", {}), snapshot.get("stage", "")
    if state.get("status") == "complete" or stage == "export":
        return "Export"
    if stage in {
        "understand",
        "research",
        "plan",
        "approve_plan",
        "save_approved_plan",
        "ready_sample",
    }:
        return "Plan"
    if (
        stage
        in {
            "sample",
            "approve_sample",
            "prepare_assets",
            "batch",
            "approve_final",
            "revise_sample",
            "revise_selected",
        }
        or state.get("sample")
        or state.get("outputs")
    ):
        return "Video review"
    return "Plan" if state.get("brief") else "Brief"


def workflow_indicator(snapshot):
    current = workflow_step(snapshot)
    steps = ["Brief", "Plan", "Video review", "Export"]
    st.caption("Saved workflow")
    st.markdown(
        " → ".join("**" + step + "**" if step == current else step for step in steps)
    )
    if snapshot["state"].get("status") == "cancelled":
        st.caption("Campaign cancelled · files retained")


@st.cache_resource(max_entries=1)
def get_service(data_dir=None, backend_revision="stage-gates-raw-clips-3"):
    from .service import CampaignService

    if backend_revision != "stage-gates-raw-clips-3":
        raise ValueError("Reload the application backend after an interface update.")
    settings = Settings(data_dir=Path(data_dir)) if data_dir else Settings()
    try:
        secrets = dict(st.secrets)
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        secrets = {}
    return CampaignService(settings, secrets=secrets, import_notebook=False)


def service():
    import os

    return get_service(os.environ.get("CFC_DATA_DIR"))


def campaign_changed():
    st.session_state["new_campaign"] = False
    st.session_state.pop("edit_campaign", None)


def current_campaign():
    cid = st.session_state.get("active_campaign")
    if not cid:
        st.info("Create a campaign brief to begin.", icon=":material/edit_note:")
        if st.button("Create a campaign", type="primary"):
            st.switch_page("app_pages/brief.py")
        st.stop()
    return cid


def stage_banner(snapshot):
    if snapshot["busy"]:
        st.caption(
            "Working in the background. Review actions return when the job finishes."
        )
    elif snapshot["state"].get("status") == "complete":
        st.caption("Final approval saved · exports ready")
    elif snapshot["state"].get("status") == "cancelled":
        st.info(
            "This campaign is cancelled. Its saved files remain available.",
            icon=":material/pause_circle:",
        )
    else:
        label, detail = STAGE_LABELS.get(
            snapshot["stage"],
            ("Campaign preparation", "Create or retry the campaign plan to continue."),
        )
        st.warning(detail, title=label, icon=":material/rate_review:")


def action(api, cid, name, snapshot, **kwargs):
    try:
        api.act(cid, name, expected_signature=snapshot["signature"], **kwargs)
    except (ValueError, RuntimeError) as error:
        st.error(str(error), icon=":material/error:")
    else:
        st.rerun()


@st.fragment(run_every=1.5)
def activity(cid):
    api = service()
    job = api.jobs.get(cid)
    if not job:
        return
    seen_key = "job_seen_" + cid
    was_running = st.session_state.get(seen_key) == (job["id"], "running")
    st.session_state[seen_key] = (job["id"], job["status"])
    if was_running and job["status"] != "running":
        st.rerun()
    if job["status"] == "running":
        st.progress(job["progress"], text=job["title"])
        if cid == "local-setup" and job["events"]:
            st.caption(job["events"][-1])
        if cid != "api-diagnostics":
            footage_tasks = [
                t
                for t in api.repository.control.tasks(cid)
                if t["kind"] == "generation"
            ]
            if footage_tasks:
                counts = {
                    status: sum(t["status"] == status for t in footage_tasks)
                    for status in [
                        "queued",
                        "generating",
                        "waiting",
                        "downloading",
                        "complete",
                        "failed",
                        "unknown",
                    ]
                }
                st.caption(
                    "AI footage · "
                    + " · ".join(
                        f"{count} {status}" for status, count in counts.items() if count
                    )
                )
        if cid not in ["api-diagnostics", "local-setup"] and st.button(
            "Stop queued and future work",
            key="stop_job_" + job["id"],
            icon=":material/stop_circle:",
        ):
            api.stop_work(cid)
            st.info(
                "Cancellation requested. Already running provider work may finish; no new work will be submitted."
            )
    if job["status"] in ["failed", "interrupted"]:
        st.error(job["error"], title="The job stopped", icon=":material/error:")
    with st.expander("Job details · " + job["title"]):
        for message in job.get("events", [])[-10:]:
            st.text(message)
        if not job.get("events"):
            st.caption("Starting the background worker…")
    if job["status"] in ["failed", "interrupted"] and cid != "api-diagnostics":
        snap = api.snapshot(cid)
        if st.button(
            "Resume future work after cancellation",
            key="resume_future_" + cid,
            disabled=snap["busy"],
        ):
            try:
                api.resume_work(cid)
            except ValueError as error:
                st.error(str(error))
            else:
                st.success(
                    "Future work enabled. Retry the interrupted step when ready."
                )
        unknown = [
            task
            for task in snap.get("tasks", [])
            if task["status"] in ["unknown", "submitting"]
        ]
        if unknown:
            with st.expander("Reconcile unknown provider submissions"):
                selected = st.selectbox(
                    "Task",
                    [task["key"] for task in unknown],
                    key="reconcile_task_" + cid,
                )
                checked = st.checkbox(
                    "I checked the provider queue/history or account usage",
                    key="checked_provider_" + cid,
                )
                not_submitted = st.checkbox(
                    "The provider confirms this request was not submitted/charged",
                    key="not_submitted_" + cid,
                )
                if st.button(
                    "Record outcome and permit bounded recovery",
                    key="reconcile_" + cid,
                    disabled=not checked or snap["busy"],
                ):
                    try:
                        st.info(api.reconcile_unknown(cid, selected, not_submitted))
                    except (ValueError, RuntimeError) as error:
                        st.error(str(error))
        if not snap["interrupted"] and snap["stage"]:
            if st.button(
                "Retry interrupted step", key="retry_" + cid, icon=":material/refresh:"
            ):
                action(api, cid, "retry", snap)


@st.cache_data(max_entries=40, ttl=600)
def file_bytes(path, modified):
    return Path(path).read_bytes()


def download(label, path, name=None, mime=None, key=None, primary=False):
    path = Path(path)
    if path.is_file():
        st.download_button(
            label,
            data=file_bytes(str(path), path.stat().st_mtime_ns),
            file_name=name or path.name,
            mime=mime,
            key=key,
            icon=":material/download:",
            width="stretch",
            type="primary" if primary else "secondary",
        )


def review_toolbar(api, cid, snapshot, location):
    stage = snapshot["stage"]
    token = snapshot["signature"]
    with st.container(horizontal=True):
        if st.button(
            "Review plan again",
            key=f"reopen_{location}_{token}",
            icon=":material/edit_note:",
            disabled=snapshot["busy"]
            or stage not in ["ready_sample", "approve_sample", "approve_final"],
        ):
            action(api, cid, "reopen_plan", snapshot)
        if st.button(
            "Cancel campaign",
            key=f"cancel_{location}_{token}",
            icon=":material/stop_circle:",
            disabled=snapshot["busy"] or not snapshot["interrupted"],
        ):
            action(api, cid, "cancel", snapshot)


def footage_upload(api, cid, snapshot):
    brief = snapshot["state"].get("brief", {})
    legacy = brief.get("schema_version", 1) < 2
    if not legacy and not brief.get("use_footage"):
        st.caption(
            "This campaign uses product images for all three videos. Lifestyle footage is optional; enable it by editing the brief."
        )
        return
    primary, secondary = (
        ("commute_walk", "commute_transit")
        if legacy
        else ("lifestyle_clip", "lifestyle_detail")
    )
    with st.container(border=True):
        st.subheader("Lifestyle footage", icon=":material/video_library:")
        st.caption(
            "Use footage you own or have permission to use in product marketing. The uploaded product images preserve product identity."
        )
        walking = st.file_uploader(
            "Main lifestyle clip",
            type=["mp4", "mov", "mkv", "webm"],
            key="walking_" + cid,
            disabled=snapshot["busy"],
        )
        transit = st.file_uploader(
            "Additional lifestyle detail (if planned)",
            type=["mp4", "mov", "mkv", "webm"],
            key="transit_" + cid,
            disabled=snapshot["busy"],
        )
        if st.button(
            "Save footage",
            key="save_footage_" + cid,
            disabled=snapshot["busy"] or not (walking or transit),
            icon=":material/upload:",
            width="stretch",
        ):
            try:
                for upload, kind in [
                    (walking, primary),
                    (transit, secondary),
                ]:
                    if upload:
                        api.save_footage(cid, upload.name, upload.getvalue(), kind)
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                st.success(
                    "Footage saved. Continue rendering when the sample is approved."
                )
        config = api.runtime(cid).renderer.commute_config
        from .storage import read_json

        files = read_json(config) if config.exists() else {}
        for kind in files:
            st.caption(
                ":material/check_circle: "
                + kind.replace("_", " ").capitalize()
                + " saved"
            )
        if snapshot["stage"] == "prepare_assets":
            if st.button(
                "Continue rendering",
                type="primary",
                disabled=snapshot["busy"] or not files.get(primary),
                key="continue_" + cid,
                width="stretch",
                icon=":material/play_arrow:",
            ):
                action(api, cid, "continue", snapshot)


def usage_panel(snapshot):
    with st.expander("Usage estimate & technical details"):
        st.json(
            dict(
                estimate=snapshot.get("estimate", {}),
                usage=snapshot.get("usage", []),
                tasks=snapshot.get("tasks", []),
            )
        )


def fact_review_panel(api, cid, snapshot):
    path = api.repository.folder(cid) / "pending_facts.json"
    if not path.exists():
        return
    from .storage import read_json

    pending = read_json(path)
    from .storage import digest

    review_key = cid + "_" + digest(pending) + "_" + snapshot["signature"]
    with st.container(border=True):
        st.warning(
            "The proposed draft contains new or uncertain statements. Verify them before approving them as product facts.",
            title="Separate fact review required",
        )
        for issue in dict.fromkeys(pending.get("issues", [])):
            st.caption(issue)
        with st.expander("Full proposed draft for fact review"):
            st.json(pending.get("plan", {}))
        if st.checkbox(
            "I verified these statements and authorize them as product facts",
            key="confirm_facts_" + review_key,
        ):
            if st.button(
                "Approve facts and save draft",
                key="approve_facts_" + review_key,
                disabled=snapshot["busy"],
            ):
                try:
                    api.approve_pending_facts(cid, snapshot["signature"])
                except (ValueError, RuntimeError) as error:
                    st.error(str(error))
                else:
                    st.rerun()


def input_panel(api, cid, snapshot):
    with st.expander("Reference text, images, video or speech"):
        st.caption(
            "Inputs are saved as context. Reference videos provide extracted frames and an optional transcript; they do not automatically become lifestyle footage or approved facts."
        )
        kind = st.selectbox(
            "Input type",
            ["text", "image", "video", "audio"],
            key="reference_kind_" + cid,
        )
        upload = st.file_uploader("Reference file", key="reference_input_" + cid)
        recording = st.audio_input(
            "Or record a spoken brief",
            key="recording_" + cid,
            disabled=snapshot["busy"],
        )
        text = st.text_area(
            "Text context or manual transcript",
            key="input_text_" + cid,
            max_chars=20000,
        )
        if text.strip() and (upload or recording):
            if st.button(
                "Save manual transcript as text",
                key="save_manual_transcript_" + cid,
                disabled=snapshot["busy"],
            ):
                try:
                    api.add_input(
                        cid,
                        "manual-transcript.txt",
                        text.encode(),
                        "text",
                        snapshot["signature"],
                    )
                except (ValueError, RuntimeError) as error:
                    st.error(str(error))
                else:
                    st.rerun()
        if st.button(
            "Analyze and save input",
            key="analyze_input_" + cid,
            disabled=snapshot["busy"] or not (upload or recording or text.strip()),
        ):
            try:
                if recording:
                    api.add_input(
                        cid,
                        "recording.wav",
                        recording.getvalue(),
                        "audio",
                        snapshot["signature"],
                    )
                elif upload:
                    api.add_input(
                        cid, upload.name, upload.getvalue(), kind, snapshot["signature"]
                    )
                else:
                    api.add_input(
                        cid,
                        "reference.txt",
                        text.encode(),
                        "text",
                        snapshot["signature"],
                    )
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                st.rerun()
        for analysis in snapshot["state"].get("brief", {}).get("input_analysis", []):
            st.json(
                {k: v for k, v in analysis.items() if k not in ["source", "frames"]}
            )
            for frame in analysis.get("frames", [])[:4]:
                if Path(frame).is_file():
                    st.image(
                        frame, width=180, alt="Extracted reference frame for planning"
                    )
    with st.expander("Add or replace product identity images"):
        main = st.file_uploader(
            "Main image to use in the next plan",
            type=["png", "jpg", "jpeg", "webp"],
            key="replacement_main_" + cid,
        )
        detail = st.file_uploader(
            "Optional second product view",
            type=["png", "jpg", "jpeg", "webp"],
            key="replacement_detail_" + cid,
        )
        if st.button(
            "Save product images and reopen approval",
            key="replace_images_" + cid,
            disabled=snapshot["busy"] or not main,
        ):
            try:
                api.add_product_images(
                    cid,
                    (main.name, main.getvalue()),
                    (detail.name, detail.getvalue()) if detail else None,
                    snapshot["signature"],
                )
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                st.rerun()
