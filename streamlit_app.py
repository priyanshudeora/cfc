"""Run with: python -m streamlit run streamlit_app.py"""

import sys
import streamlit as st

st.set_page_config(
    page_title="CFC video studio", page_icon=":material/movie:", layout="wide"
)
if sys.version_info[:2] != (3, 14):
    st.error("Run this app with the project .venv using Python 3.14.7.")
    st.stop()

from video_agent.ui import service

page = st.navigation(
    [
        st.Page(
            "app_pages/overview.py",
            title="Overview",
            icon=":material/space_dashboard:",
            default=True,
        ),
        st.Page(
            "app_pages/brief.py", title="Campaign brief", icon=":material/edit_note:"
        ),
        st.Page(
            "app_pages/plan.py", title="Plan & approvals", icon=":material/fact_check:"
        ),
        st.Page("app_pages/videos.py", title="Video review", icon=":material/movie:"),
        st.Page(
            "app_pages/connections.py", title="API connections", icon=":material/cable:"
        ),
        st.Page(
            "app_pages/architecture.py",
            title="Architecture",
            icon=":material/account_tree:",
        ),
    ],
    position="top",
)

api = service()
projects = api.list_campaigns()
options = [p["campaign_id"] for p in projects]
names = {
    p["campaign_id"]: " · ".join(filter(None, [p["name"], p.get("product")]))
    for p in projects
}
for cid, label in list(names.items()):
    if list(names.values()).count(label) > 1:
        names[cid] = label + " · " + cid[-8:]
st.session_state.setdefault("active_campaign", options[0] if options else None)
st.session_state.setdefault("new_campaign", False)
if st.session_state.get("pending_campaign"):
    st.session_state["active_campaign"] = st.session_state.pop("pending_campaign")
if st.session_state["active_campaign"] not in options:
    st.session_state["active_campaign"] = options[0] if options else None
    st.session_state.pop("edit_campaign", None)

with st.sidebar:
    st.title("CFC studio", icon=":material/slow_motion_video:")
    st.caption("A brief. Three stories. Your approval.")
    st.divider()
    if options:
        st.selectbox(
            "Open campaign", options, format_func=names.get, key="active_campaign"
        )
    if st.button(
        "New campaign",
        icon=":material/add:",
        width="stretch",
        key="new_campaign_button",
    ):
        st.session_state["new_campaign"] = True
        st.switch_page("app_pages/brief.py")
    st.divider()
    st.caption("20 seconds · 9:16 · English")
    st.caption("Local voice, original music and exact product images.")

st.caption("CFC VIDEO STUDIO  /  CREATIVE WORKSPACE")
st.title(page.title, icon=page.icon)
page.run()
