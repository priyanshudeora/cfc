import json
import streamlit as st
from video_agent.ui import service, current_campaign

api, cid = service(), current_campaign()
graph = api.runtime(cid).agent.get_graph()
snapshot = api.snapshot(cid)
st.markdown(
    "Each approval is a durable checkpoint. Rendering checks the saved plan signature before producing media."
)
labels = {
    "__start__": "Start",
    "__end__": "End",
    "understand": "Validate brief",
    "research": "Reference research",
    "plan": "Write / revise plan",
    "approve_plan": "Approve plan",
    "save_approved_plan": "Save approved scripts",
    "ready_sample": "Generate sample?",
    "sample": "Local / API voice + render",
    "approve_sample": "Approve sample",
    "revise_sample": "Revise sample script",
    "prepare_assets": "Check planned footage",
    "batch": "Render remaining videos",
    "approve_final": "Approve final videos",
    "revise_selected": "Revise selected script",
    "export": "Prepare delivery bundle",
}
lines = [
    "digraph workflow {",
    "rankdir=LR;",
    'bgcolor="transparent";',
    'node [shape=box, style="rounded,filled", color="#D9DEE8", fontname="Arial", fillcolor="#F4F6FA"];',
    'edge [color="#8A96A8", fontname="Arial", fontsize=10];',
]
for name in graph.nodes:
    color = (
        "#FFF0D8"
        if name.startswith("approve")
        else "#E3F3EE"
        if name == snapshot["stage"]
        else "#F4F6FA"
    )
    lines.append(
        f'{json.dumps(name)} [label={json.dumps(labels.get(name, name))},fillcolor="{color}"];'
    )
for edge in graph.edges:
    lines.append(
        f"{json.dumps(edge.source)} -> {json.dumps(edge.target)} [label={json.dumps(edge.data or '')}];"
    )
lines.append("}")
st.graphviz_chart(
    "\n".join(lines),
    width="stretch",
    height=520,
    alt="Actual campaign graph, including plan, sample and final approval checkpoints and revision loops",
)
st.caption("Orange: approval checkpoints · Green: current step")
with st.container(border=True):
    st.subheader("Application modules")
    st.code(
        "streamlit_app.py        navigation and workspace\napp_pages/              brief, plans, video review and API checks\nvideo_agent/service.py  validated user actions and background jobs\nvideo_agent/workflow.py LangGraph checkpoints and approval gates\nvideo_agent/planning.py Gemini writer, reviewer and revisions\nvideo_agent/rendering/  speech, music, captions and video export\nvideo_agent/storage.py atomic files and versioned approvals",
        language="text",
    )
