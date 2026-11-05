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
    st.subheader("Implemented execution path")
    st.markdown(
        "**Input analysis → saved brief → factual/script review → single-use plan approval → scoped sample → rendered-media review → scoped batch → exact-version final approval.**"
    )
    st.markdown(
        "SQLite stores review IDs, checkpoint/content bindings, stage grants, worker leases, scene tasks and usage reservations. FFmpeg executes validated edits, transitions, caption burning and audio mixing. Scene and speech caches use content dependencies, independently of review IDs and campaign versions."
    )
    st.caption(
        "Text and reference-image analysis are local. Video analysis extracts frames and optional transcripts. Models and supplied inputs are data, never approval authority."
    )
with st.container(border=True):
    st.subheader("Optional integrations and future work")
    st.markdown(
        "**Optional:** Gemini planning with account-access checks, ElevenLabs speech with a character allowance, local whisper.cpp transcription, and a local ComfyUI footage queue with workflow/model/GPU checks. They are not required for image-based motion graphics."
    )
    st.markdown(
        "**Future:** external editing-application MCP and Jev integration. Neither is connected or represented by the local Python functions. Semantic product-appearance and advertising-performance review are not automated; human media review remains required."
    )
with st.container(border=True):
    st.subheader("Application modules")
    st.code(
        "streamlit_app.py        navigation and workspace\napp_pages/              brief, plans, media review and API checks\nvideo_agent/control.py  atomic reviews, grants, leases, tasks and allowances\nvideo_agent/workflow.py LangGraph approval and execution checkpoints\nvideo_agent/facts.py    conservative claims and spoken quantities\nvideo_agent/timeline.py validated executable edits\nvideo_agent/cache.py    verified scene and speech reuse\nvideo_agent/inputs.py   cached multimodal input analysis\nvideo_agent/generation.py optional local ComfyUI queue\nvideo_agent/planning.py writer, reviewer, model access and research\nvideo_agent/rendering/ FFmpeg editing, speech, music and media review",
        language="text",
    )
