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


@st.cache_resource(max_entries=1)
def get_service(data_dir=None):
    from .service import CampaignService

    settings = Settings(data_dir=Path(data_dir)) if data_dir else Settings()
    try:
        secrets = dict(st.secrets)
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        secrets = {}
    return CampaignService(settings, secrets=secrets, import_notebook=False)


def service():
    import os

    return get_service(os.environ.get("CFC_DATA_DIR"))


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
        st.info(
            "A background job is running. Progress appears below; review actions return when it finishes.",
            title="Work in progress",
            icon=":material/pending:",
        )
    elif snapshot["state"].get("status") == "complete":
        st.success(
            "Your approved MP4s, captions and campaign bundle are ready.",
            title="Ready to share",
            icon=":material/task_alt:",
        )
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
    if job["status"] in ["failed", "interrupted"]:
        st.error(job["error"], title="The job stopped", icon=":material/error:")
    with st.expander("Activity · " + job["title"], expanded=job["status"] == "running"):
        for message in job.get("events", [])[-10:]:
            st.text(message)
        if not job.get("events"):
            st.caption("Starting the background worker…")
    if job["status"] in ["failed", "interrupted"] and cid != "api-diagnostics":
        snap = api.snapshot(cid)
        if not snap["interrupted"] and snap["stage"]:
            if st.button(
                "Retry interrupted step", key="retry_" + cid, icon=":material/refresh:"
            ):
                action(api, cid, "retry", snap)


@st.cache_data(max_entries=40, ttl=600)
def file_bytes(path, modified):
    return Path(path).read_bytes()


def download(label, path, name=None, mime=None, key=None):
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
        )


def review_toolbar(api, cid, snapshot, location):
    state, stage = snapshot["state"], snapshot["stage"]
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
