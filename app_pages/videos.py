from pathlib import Path
import streamlit as st
from video_agent.ui import (
    service,
    current_campaign,
    stage_banner,
    activity,
    action,
    download,
    review_toolbar,
    footage_upload,
    usage_panel,
)

api, cid = service(), current_campaign()
snapshot = api.snapshot(cid)
state, token = snapshot["state"], snapshot["signature"]
stage_banner(snapshot)
activity(cid)
usage_panel(snapshot)
if state.get("prior_outputs"):
    with st.expander("Earlier output versions"):
        for index, previous in enumerate(state["prior_outputs"]):
            st.caption(
                f"Version {previous.get('version', '?')} · {previous.get('title', 'Video')}"
            )
            download(
                "Download earlier MP4",
                previous["video"],
                key=f"prior_{cid}_{index}",
                mime="video/mp4",
            )
outputs = state.get("outputs") or ([state["sample"]] if state.get("sample") else [])
if not outputs:
    st.info(
        "Your first video appears here after you approve the plan and choose Generate representative sample."
    )
    if st.button("Open plan review", type="primary", icon=":material/arrow_forward:"):
        st.switch_page("app_pages/plan.py")
else:
    selected = st.pills(
        "Video preview",
        list(range(len(outputs))),
        default=0,
        format_func=lambda i: f"{i + 1}. {outputs[i]['title']}",
        key="preview_" + cid,
    )
    selected = 0 if selected is None else selected
    result = outputs[selected]
    left, right = st.columns([1, 1.3], gap="large")
    with left:
        if Path(result["video"]).is_file():
            st.video(
                result["video"],
                width=360,
                alt="20-second vertical product video: " + result["title"],
            )
        else:
            st.error(
                "This preview file is missing. Reopen plan review to regenerate it."
            )
    with right:
        st.subheader(result["title"])
        st.caption("20 seconds · 1080 × 1920 · H.264 / AAC · English")
        st.markdown(
            "Listen for clear pronunciation and check the pacing, product appearance, caption size and ending call to action."
        )
        if snapshot["stage"] == "approve_sample":
            st.markdown(
                "**This is the representative sample.** Your approval unlocks the remaining two videos."
            )
            reviewed = st.checkbox(
                "I reviewed the actual product appearance, caption readability, pronunciation and music balance",
                key="media_checked_" + token,
            )
            if st.button(
                "Approve sample",
                type="primary",
                key="approve_sample_" + token,
                disabled=snapshot["busy"] or not snapshot["approved"] or not reviewed,
                width="stretch",
                icon=":material/task_alt:",
            ):
                action(api, cid, "approve", snapshot)
        if snapshot["stage"] == "approve_final":
            final_checked = st.checkbox(
                "I reviewed all selected output versions", key="final_checked_" + token
            )
            if st.button(
                "Approve final videos",
                type="primary",
                key="approve_final_" + token,
                disabled=snapshot["busy"]
                or not snapshot["approved"]
                or not final_checked,
                width="stretch",
                icon=":material/task_alt:",
            ):
                action(api, cid, "approve", snapshot)
        with st.expander("Listen to narration only"):
            if Path(result.get("voice", "")).is_file():
                st.audio(
                    result["voice"],
                    alt="Narration without music for pronunciation review",
                )
            else:
                st.caption("A separate voice track is not available for this preview.")
        with st.expander("Export checks"):
            st.caption(result.get("quality_check", "Saved preview"))
            if result.get("quality"):
                st.json(result["quality"])
            if Path(result.get("contact_sheet", "")).is_file():
                st.image(
                    result["contact_sheet"],
                    alt="Frames extracted from the actual rendered video for human review",
                )
        download(
            "Download MP4 preview",
            result["video"],
            mime="video/mp4",
            key=f"mp4_{token}_{selected}",
        )
        if result.get("captions"):
            download(
                "Download captions",
                result["captions"],
                mime="application/x-subrip",
                key=f"srt_{token}_{selected}",
            )
    if snapshot["stage"] in ["approve_sample", "approve_final"]:
        with st.form("video_feedback_" + token):
            st.subheader("Request a change", icon=":material/edit_note:")
            feedback = st.text_area(
                "What should change in this video?",
                max_chars=2000,
                placeholder="Describe the hook, script or visual changes you want.",
            )
            st.caption(
                "Script changes return to plan approval before new media is rendered."
            )
            submitted = st.form_submit_button(
                "Submit video changes", disabled=snapshot["busy"], width="stretch"
            )
        if submitted:
            action(
                api,
                cid,
                "revise",
                snapshot,
                feedback=feedback,
                index=0 if snapshot["stage"] == "approve_sample" else selected,
            )
    if state.get("status") == "complete" and state.get("export_path"):
        st.success(
            "Final approval is saved. This bundle contains the three MP4s, captions and approved plan.",
            title="Delivery ready",
            icon=":material/inventory_2:",
        )
        download(
            "Download the full campaign",
            state["export_path"],
            mime="application/zip",
            key="bundle_" + token,
        )

if snapshot["stage"] == "prepare_assets":
    footage_upload(api, cid, snapshot)
review_toolbar(api, cid, snapshot, "videos")
