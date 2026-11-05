import copy
import streamlit as st
from video_agent.ui import (
    service,
    current_campaign,
    stage_banner,
    activity,
    action,
    review_toolbar,
    download,
    FORMAT_NAMES,
    usage_panel,
    fact_review_panel,
)

api, cid = service(), current_campaign()
snapshot = api.snapshot(cid)
state, token = snapshot["state"], snapshot["signature"]
stage_banner(snapshot)
activity(cid)
fact_review_panel(api, cid, snapshot)
if snapshot["stage"] == "plan" and not snapshot["busy"]:
    if st.button(
        "Use saved draft instead", icon=":material/history:", key="saved_draft_" + cid
    ):
        try:
            api.use_saved_draft(cid, token)
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        else:
            st.rerun()
if not state.get("plan"):
    st.info("Your concepts will appear here when planning finishes.")
    st.stop()

with st.container(horizontal=True):
    st.badge("Version " + str(state["version"]), icon=":material/history:")
    st.badge(
        "Approved and saved" if snapshot["approved"] else "Awaiting approval",
        color="green" if snapshot["approved"] else "orange",
    )
if snapshot.get("missing_assets"):
    st.info(
        "Missing planned assets: "
        + ", ".join(snapshot["missing_assets"])
        + ". Add them in Campaign brief before generation."
    )
with st.expander("Campaign direction"):
    st.markdown(state["plan"]["summary"])

for col, ad in zip(st.columns(3), state["plan"]["ads"]):
    with col.container(border=True, height="stretch"):
        st.caption(FORMAT_NAMES[ad["video_type"]].upper())
        st.subheader(ad["title"])
        st.markdown(ad["concept"])
        st.caption("Opening · " + ad["scenes"][0]["headline"])

if snapshot["stage"] == "approve_plan":
    if st.button(
        "Approve plan and save",
        type="primary",
        icon=":material/task_alt:",
        width="content",
        disabled=snapshot["busy"],
        key="approve_plan_" + token,
    ):
        action(api, cid, "approve", snapshot)
elif snapshot["stage"] == "ready_sample":
    if st.button(
        "Generate representative sample",
        type="primary",
        icon=":material/movie:",
        width="content",
        disabled=snapshot["busy"]
        or not snapshot["approved"]
        or bool(snapshot.get("missing_assets")),
        key="generate_" + token,
    ):
        action(api, cid, "generate", snapshot)
else:
    if st.button(
        "Continue to video review",
        type="primary",
        icon=":material/arrow_forward:",
        width="content",
    ):
        st.switch_page("app_pages/videos.py")

view = st.segmented_control(
    "Plan detail",
    ["Scene plans", "Request changes", "Edit script", "Timeline", "References"],
    default="Scene plans",
    key="plan_detail_" + cid,
)
ads = state["plan"]["ads"]
if view in ["Scene plans", "Edit script"]:
    selected = st.pills(
        "Video",
        list(range(3)),
        default=0,
        format_func=lambda i: FORMAT_NAMES[ads[i]["video_type"]],
        key="plan_ad_" + cid,
    )
    selected = 0 if selected is None else selected
    ad = ads[selected]
    if view == "Scene plans":
        st.markdown("### " + ad["title"])
        st.caption("Music direction: " + ad["music_direction"])
        elapsed = 0
        for index, scene in enumerate(ad["scenes"], 1):
            with st.container(border=True):
                st.caption(
                    f"SCENE {index:02d}  /  {elapsed:02d}–{elapsed + scene['seconds']:02d} SECONDS"
                )
                st.markdown("**" + scene["headline"] + "**")
                left, right = st.columns([1, 1])
                left.markdown("**Voiceover and captions**  \n" + scene["narration"])
                right.markdown("**Visual direction**  \n" + scene["visual_direction"])
                if scene["footnote"]:
                    st.caption(scene["footnote"])
            elapsed += scene["seconds"]
        st.caption("Required assets: " + ", ".join(ad["assets_required"]))
    else:
        st.caption(
            "Save a new draft locally. The complete revised plan needs approval before any new rendering."
        )
        edited = copy.deepcopy(state["plan"])
        with st.form("edit_script_" + token + "_" + str(selected)):
            for i, scene in enumerate(edited["ads"][selected]["scenes"]):
                st.markdown(f"**Scene {i + 1} · {scene['seconds']} seconds**")
                scene["headline"] = st.text_input(
                    "Headline",
                    value=scene["headline"],
                    disabled=i == 4,
                    key=f"headline_{cid}_{token}_{selected}_{i}",
                )
                scene["narration"] = st.text_area(
                    "Voiceover and caption",
                    value=scene["narration"],
                    disabled=i == 4,
                    key=f"narration_{cid}_{token}_{selected}_{i}",
                )
                scene["caption"] = scene["narration"]
                if state["brief"].get("schema_version", 1) >= 2:
                    facts = state["brief"].get("approved_claims", {})
                    scene["claim_ids"] = st.multiselect(
                        "Facts used in this scene",
                        list(facts),
                        default=scene["claim_ids"],
                        format_func=lambda key: key + ": " + facts[key],
                        key=f"claims_{cid}_{token}_{selected}_{i}",
                    )
                    scene["footnote"] = st.text_area(
                        "Fact qualification or footnote",
                        value=scene["footnote"],
                        max_chars=160,
                        key=f"footnote_{cid}_{token}_{selected}_{i}",
                    )
                    sources = [
                        key
                        for key in state["brief"].get("asset_ids", [])
                        if not key.startswith("style_")
                    ]
                    if state["brief"].get("use_footage"):
                        sources += ["lifestyle_clip", "lifestyle_detail"]
                    chosen = st.selectbox(
                        "Visual source",
                        sources,
                        index=sources.index(scene["asset_ids"][0]),
                        key=f"source_{cid}_{token}_{selected}_{i}",
                    )
                    scene["asset_ids"] = [chosen]
                scene["visual_direction"] = st.text_area(
                    "Visual direction",
                    value=scene["visual_direction"],
                    key=f"visual_{cid}_{token}_{selected}_{i}",
                    disabled=True,
                    help="Descriptive note. Apply executable motion, layout and audio edits in Timeline.",
                )
            edited["ads"][selected]["assets_required"] = list(
                dict.fromkeys(
                    [
                        key
                        for scene in edited["ads"][selected]["scenes"]
                        for key in scene["asset_ids"]
                    ]
                    + ["voiceover", "original_music", "captions"]
                )
            )
            save = st.form_submit_button(
                "Save revised draft",
                type="primary",
                disabled=snapshot["busy"],
                width="stretch",
            )
        if save:
            try:
                api.save_manual_plan(cid, edited, token)
            except (ValueError, RuntimeError) as error:
                st.error(str(error), title="The draft needs changes")
            else:
                st.rerun()
elif view == "Timeline":
    import json
    from video_agent.models import AdPlan

    selected = st.selectbox(
        "Video to edit",
        list(range(3)),
        format_func=lambda i: ads[i]["title"],
        key="timeline_ad_" + cid,
    )
    st.caption(
        "These parameters execute in FFmpeg. Local feedback supports: scene 2 move product left; zoom in; fade transitions; quieter music; faster beat. Unsupported instructions are rejected."
    )
    feedback = st.text_input("Local timeline feedback", key="timeline_feedback_" + cid)
    if st.button(
        "Apply local timeline edit",
        key="apply_timeline_" + cid,
        disabled=snapshot["busy"],
    ):
        try:
            api.edit_timeline(cid, selected, feedback, token)
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        else:
            st.rerun()
    with (
        st.expander("Advanced: structured timeline JSON"),
        st.form("structured_timeline_" + token),
    ):
        timeline = st.text_area(
            "Structured video timeline JSON",
            value=json.dumps(
                AdPlan.model_validate(ads[selected]).model_dump(), indent=2
            ),
            height=260,
        )
        submitted = st.form_submit_button(
            "Validate and save timeline", disabled=snapshot["busy"]
        )
    if submitted:
        try:
            edited = copy.deepcopy(state["plan"])
            edited["ads"][selected] = AdPlan.model_validate_json(timeline).model_dump()
            api.save_manual_plan(cid, edited, token)
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        else:
            st.rerun()
elif view == "Request changes":
    with st.form("revise_plan_" + token):
        feedback = st.text_area(
            "What should change in the concepts, scripts or scenes?",
            placeholder="For example: use a stronger opening question and give the product details more screen time.",
            max_chars=2500,
        )
        st.caption(
            "This sends your feedback and the current plan to Gemini. The revised draft returns here for approval."
        )
        submitted = st.form_submit_button(
            "Request revised plan",
            type="primary",
            disabled=snapshot["busy"],
            width="stretch",
        )
    if submitted:
        action(api, cid, "revise_plan", snapshot, feedback=feedback)
else:
    if st.button(
        "Refresh public competitor sources",
        key="refresh_sources_" + cid,
        disabled=snapshot["busy"],
    ):
        try:
            api.refresh_research(cid, token)
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        else:
            st.rerun()
    for source in state.get("research", []):
        if source.get("url"):
            with st.container(border=True):
                st.markdown(
                    f"**[{source.get('title', source.get('competitor', 'Source'))}]({source['url']})**"
                )
                st.caption(
                    source.get(
                        "reference_type", source.get("checked_at", "Saved reference")
                    )
                )
                st.markdown(source.get("observation", source.get("excerpt", "")))
    st.caption(
        "Reference images and research are inputs; only the approved product claims authorize script claims."
    )

if snapshot["approved"]:
    path = api.repository.approved_path(state)
    with st.container(horizontal=True):
        download(
            "Approved plan JSON",
            path,
            mime="application/json",
            key="plan_json_" + token,
        )
        download(
            "Readable scripts",
            path.with_suffix(".md"),
            mime="text/markdown",
            key="plan_md_" + token,
        )
usage_panel(snapshot)
with st.expander("Campaign actions"):
    review_toolbar(api, cid, snapshot, "plan")
