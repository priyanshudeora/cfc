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
    FORMAT_NAMES,
)

api, cid = service(), current_campaign()
snapshot = api.snapshot(cid)
state, token = snapshot["state"], snapshot["signature"]
stage_banner(snapshot)
activity(cid)

if state.get("status") == "complete":
    with st.container(border=True):
        st.subheader("Your campaign is ready", icon=":material/task_alt:")
        if Path(state.get("export_path", "")).is_file():
            st.caption(
                "Final approval is saved. Get all three MP4s, captions and the approved plan in one ZIP."
            )
            download(
                "Download the full campaign",
                state["export_path"],
                mime="application/zip",
                key="bundle_" + token,
                primary=True,
            )
        else:
            st.error(
                "Final approval is saved, but the campaign bundle is missing. Individual files below may still be available."
            )

outputs = state.get("outputs") or ([state["sample"]] if state.get("sample") else [])
if not outputs:
    if snapshot["busy"]:
        st.info(
            "Your preview will appear here when the current job finishes. You can leave this page while it runs."
        )
    else:
        st.info(
            "No preview yet. Approve the plan, then generate your representative sample."
        )
        if st.button(
            "Open plan review", type="primary", icon=":material/arrow_forward:"
        ):
            st.switch_page("app_pages/plan.py")
else:
    preview_key = "preview_" + cid
    if st.session_state.get(preview_key, 0) not in range(len(outputs)):
        st.session_state[preview_key] = 0
    selected = st.segmented_control(
        "Choose a video",
        list(range(len(outputs))),
        default=0,
        format_func=lambda i: (
            f"{i + 1:02d}  {FORMAT_NAMES.get(state['plan']['ads'][i]['video_type'], outputs[i]['title'])}"
        ),
        key=preview_key,
    )
    selected = 0 if selected is None else selected
    result = outputs[selected]
    available = Path(result["video"]).is_file()
    all_available = all(
        Path(item["video"]).is_file() and Path(item.get("captions", "")).is_file()
        for item in outputs
    )
    if snapshot["stage"] == "approve_final" and not all_available:
        st.warning(
            "A required MP4 or caption file is missing. Final approval is blocked; inspect each video or save a new draft to regenerate the missing files."
        )
    player, review = st.columns([1.05, 1], gap="large")
    with player:
        with st.container(horizontal_alignment="center"):
            if available:
                st.video(
                    result["video"],
                    width=440,
                    alt="Portrait marketing video: " + result["title"],
                )
            else:
                st.error(
                    "This MP4 is missing. Open the plan and save a new draft to regenerate it. Earlier versions may still be available below."
                )
                st.page_link(
                    "app_pages/plan.py",
                    label="Open plan & approvals",
                    icon=":material/fact_check:",
                )
    with review:
        with st.container():
            st.subheader(result["title"])
            st.caption(
                f"Video {selected + 1} of {len(outputs)} · Version {result.get('version', state['version'])} · 20 seconds · English"
            )
            st.markdown("**Review the actual video**")
            st.caption(
                "Check the product, readable captions, pronunciation, music balance and ending call to action."
            )
            if snapshot["stage"] == "approve_sample":
                cpu_batch_blocked = (
                    state["brief"].get("generation", {}).get("batch_enabled") is False
                    and snapshot["estimate"].get("generation_jobs", 0) > 1
                )
                if cpu_batch_blocked:
                    st.warning(
                        "Batch inference is disabled for this profile. A measured batch profile and fresh plan approval are required before creating the remaining videos."
                    )
                st.caption(
                    "Approving this sample authorizes the remaining two videos within the saved plan."
                )
                reviewed = st.checkbox(
                    "I checked the visuals, captions and audio",
                    key="media_checked_" + token,
                    disabled=not available or snapshot["busy"],
                )
                if st.button(
                    "Approve sample",
                    type="primary",
                    key="approve_sample_" + token,
                    disabled=snapshot["busy"]
                    or not snapshot["approved"]
                    or not reviewed
                    or not available
                    or cpu_batch_blocked,
                    # Scope enforcement is repeated in CampaignService.act.
                    width="stretch",
                    icon=":material/task_alt:",
                ):
                    action(api, cid, "approve", snapshot)
            elif snapshot["stage"] == "approve_final":
                st.caption("Switch between all three videos before approving this set.")
                final_checked = st.checkbox(
                    "I reviewed all selected output versions",
                    key="final_checked_" + token,
                    disabled=not all_available or snapshot["busy"],
                )
                if st.button(
                    "Approve final videos",
                    type="primary",
                    key="approve_final_" + token,
                    disabled=snapshot["busy"]
                    or not snapshot["approved"]
                    or not final_checked
                    or not all_available,
                    width="stretch",
                    icon=":material/task_alt:",
                ):
                    action(api, cid, "approve", snapshot)

            with st.expander("Request a change"):
                st.caption(
                    "Local layout or audio edits create a new draft for approval."
                )
                if st.button(
                    "Edit layout or audio locally",
                    key="local_edit_" + token + str(selected),
                    disabled=snapshot["busy"],
                ):
                    st.session_state["plan_detail_" + cid] = "Timeline"
                    st.session_state["timeline_ad_" + cid] = selected
                    st.switch_page("app_pages/plan.py")
                if snapshot["stage"] in ["approve_sample", "approve_final"]:
                    with st.form("video_feedback_" + token + "_" + str(selected)):
                        feedback = st.text_area(
                            "What should change in this video's script?",
                            max_chars=2000,
                            height=100,
                            placeholder="Describe the opening, wording or pacing you want.",
                        )
                        st.caption(
                            "Script revisions use your configured AI provider and return to plan approval. They may use account quota."
                        )
                        submitted = st.form_submit_button(
                            "Submit video changes", disabled=snapshot["busy"]
                        )
                    if submitted:
                        action(
                            api,
                            cid,
                            "revise",
                            snapshot,
                            feedback=feedback,
                            index=0
                            if snapshot["stage"] == "approve_sample"
                            else selected,
                        )

            st.markdown("**Downloads**")
            mp4, captions = st.columns(2)
            with mp4:
                if available:
                    download(
                        "Download MP4",
                        result["video"],
                        mime="video/mp4",
                        key=f"mp4_{token}_{selected}",
                    )
                else:
                    st.caption("MP4 unavailable")
            with captions:
                if Path(result.get("captions", "")).is_file():
                    download(
                        "Download captions",
                        result["captions"],
                        mime="application/x-subrip",
                        key=f"srt_{token}_{selected}",
                    )
                else:
                    st.caption("Captions unavailable")
            with st.expander("Listen to narration only"):
                if Path(result.get("voice", "")).is_file():
                    st.audio(
                        result["voice"],
                        alt="Narration without music for pronunciation review",
                    )
                else:
                    st.caption(
                        "A separate voice track is not available for this preview."
                    )
            with st.expander("Export checks & contact sheet"):
                for i, source in enumerate(
                    result.get("visual_source", {}).get("scenes", []), 1
                ):
                    st.caption(
                        f"Scene {i}: {source.get('source', 'source not recorded')}"
                    )
                st.caption(result.get("quality_check", "Saved preview"))
                if result.get("quality"):
                    st.json(result["quality"])
                if Path(result.get("contact_sheet", "")).is_file():
                    st.image(
                        result["contact_sheet"],
                        alt="Frames extracted from the actual video for human review",
                    )

if snapshot["stage"] == "prepare_assets":
    footage_upload(api, cid, snapshot)
if state.get("prior_outputs"):
    with st.expander("Earlier output versions"):
        for index, previous in enumerate(state["prior_outputs"]):
            st.caption(
                f"Version {previous.get('version', '?')} · {previous.get('title', 'Video')}"
            )
            if Path(previous["video"]).is_file():
                download(
                    "Download earlier MP4",
                    previous["video"],
                    key=f"prior_{cid}_{index}",
                    mime="video/mp4",
                )
            else:
                st.caption("This earlier file is no longer available.")
usage_panel(snapshot)
with st.expander("Campaign actions"):
    review_toolbar(api, cid, snapshot, "videos")
