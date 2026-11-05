from pathlib import Path
import streamlit as st
from video_agent.ui import service, current_campaign, stage_banner, activity

api = service()
if not st.session_state.get("active_campaign"):
    st.subheader("Your next campaign starts here", icon=":material/campaign:")
    st.markdown(
        "Add your product images, audience and approved facts. Review three concepts, "
        "approve a sample, then create your videos."
    )
    st.caption("Product demonstration · Lifestyle story · Product reveal")
    if st.button(
        "Create a campaign",
        type="primary",
        icon=":material/add:",
        key="empty_create_campaign",
    ):
        st.session_state["new_campaign"] = True
        st.switch_page("app_pages/brief.py")
    st.stop()
cid = current_campaign()
snapshot = api.snapshot(cid)
state = snapshot["state"]
st.markdown(
    "Create campaigns for any product. Choose New campaign to add your own brand, images and approved facts, or continue the selected campaign below."
)
stage_banner(snapshot)

columns = st.columns(3)
columns[0].metric("Videos in this campaign", "3", border=True)
columns[1].metric(
    "Preview files ready",
    str(
        len(state.get("outputs") or ([state["sample"]] if state.get("sample") else []))
    ),
    border=True,
)
columns[2].metric("Plan version", str(state.get("version", 0)), border=True)

left, right = st.columns([1.35, 1], gap="large")
with left:
    with st.container(border=True):
        st.subheader("Your product campaign", icon=":material/campaign:")
        brief = state.get("brief", {})
        st.markdown("### " + brief.get("product", "Product"))
        st.caption(" · ".join(filter(None, [brief.get("brand"), brief.get("variant")])))
        st.markdown("**Audience**  \n" + brief.get("audience", ""))
        st.markdown("**Campaign goal**  \n" + brief.get("goal", ""))
        st.divider()
        target = (
            "app_pages/plan.py"
            if snapshot["stage"] in ["approve_plan", "ready_sample"]
            else "app_pages/videos.py"
        )
        label = "Review the plan" if target.endswith("plan.py") else "Open video review"
        if st.button(
            label, type="primary", icon=":material/arrow_forward:", width="stretch"
        ):
            st.switch_page(target)
    with st.container(border=True):
        st.subheader("Three different openings")
        for index, ad in enumerate(state.get("plan", {}).get("ads", []), 1):
            st.markdown(f"**0{index} / {ad['title']}**")
            st.caption(ad["scenes"][0]["headline"])
with right:
    sample = state.get("sample")
    if sample and Path(sample["video"]).is_file():
        st.caption("CURRENT REPRESENTATIVE SAMPLE")
        st.video(
            sample["video"],
            width=300,
            alt="Current product demonstration sample",
        )
    else:
        images = [
            a["path"]
            for a in api.repository.assets(cid).values()
            if a["use"] == "identity"
        ]
        if images:
            st.image(
                images[0],
                width=360,
                alt="Supplied product image for the current campaign",
            )
            st.caption("The supplied product image is the visual source of truth.")
        else:
            st.info(
                "Plan with the available context, then add product identity images in Campaign brief before generation."
            )

activity(cid)
