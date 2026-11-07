from pathlib import Path
import streamlit as st
from video_agent.config import ROOT
from video_agent.ui import service, activity
from video_agent.connections import PROVIDERS
from video_agent.storage import read_json

api = service()
keys = api.settings.keys(api.secrets)
st.subheader("Local production readiness")
st.caption(
    "Editing, speech and AI footage are separate capabilities. Editing-only campaigns need no ComfyUI; narrated exports still require the selected voice engine."
)
from video_agent.preflight import editing_readiness, voice_readiness

setup_path = api.settings.data_dir / "local_setup.json"
saved_setup = read_json(setup_path) if setup_path.exists() else {}
footage_hint = dict(
    available=None,
    message="Profile files present; connectivity, actual device, memory and workflow have not been checked. Start the measured server using docs/local-generation.md, then Check local setup. Editing does not require it.",
)
for variable in ["COMFY_WORKFLOW", "COMFY_PROFILE"]:
    if not Path(keys.get(variable) or "__missing__").is_file():
        footage_hint = dict(
            available=False,
            message=f"{variable} is missing or its file is unavailable. Set up ComfyUI with python scripts/setup_comfy.py --root C:\\CFC-Comfy --device cuda --download-models, then run verify_comfy_workflow.py and register_comfy_profile.py as documented in docs/local-generation.md. Set the measured workflow/profile path in .env; setup alone is not verification.",
        )
        break
local_results = saved_setup.get("results") or dict(
    editing=editing_readiness(),
    voice=voice_readiness(api.settings.model_dir, keys),
    footage=footage_hint,
)
if saved_setup:
    st.caption(
        "Recorded check: "
        + saved_setup["checked_at"]
        + ". Recheck after installing or changing files/server settings."
    )
for col, (component, title) in zip(
    st.columns(3),
    [("editing", "Editing"), ("voice", "Local voice"), ("footage", "AI footage")],
):
    with col.container(border=True, height="stretch"):
        result = local_results[component]
        st.markdown("**" + title + "**")
        st.badge(
            "Ready"
            if result["available"] is True
            else "Missing / unavailable"
            if result["available"] is False
            else "Verification needed",
            color="green" if result["available"] is True else "orange",
        )
        st.caption(result["message"])
with st.container(horizontal=True):
    if st.button(
        "Check local setup",
        key="check_local_setup",
        disabled=api.jobs.busy("local-setup"),
    ):
        api.check_local_setup()
        st.rerun()
    if st.button(
        "Set up local voice / download models",
        key="setup_local_voice",
        disabled=api.jobs.busy("local-setup"),
    ):
        api.setup_local_voice()
        st.rerun()
st.caption(
    "Explicit setup installs public, hash-locked Kokoro dependencies in a separate Python 3.12 environment and downloads about 120 MB of SHA-256-verified models. Existing unowned environments are never overwritten. It does not generate footage or approve a campaign."
)
st.code(
    "python -m pip install uv==0.12.19\npython scripts\\setup_tts.py --download-models",
    language="bash",
)
activity("local-setup")
st.markdown(
    "Check each provider with a small request and inspect the actual response. Keys stay in `.env` or Streamlit secrets."
)
path = api.settings.data_dir / "api_checks.json"
results = read_json(path) if path.exists() else {}

for col, (provider, key) in zip(st.columns(4), PROVIDERS.items()):
    with col.container(border=True, height="stretch"):
        st.subheader(provider)
        st.badge(
            "Key configured" if keys.get(key) else "Key missing",
            color="green" if keys.get(key) else "orange",
        )
        previous = results.get(provider)
        st.caption(
            "Last check: HTTP " + str(previous["status"])
            if previous
            else "Not checked in this app yet"
        )

selected = st.pills(
    "Providers to check",
    list(PROVIDERS),
    selection_mode="multi",
    default=list(PROVIDERS),
    key="api_selection",
)
st.caption(
    "Gemini generates one short test reply. Tavily performs one basic search. Coverr retrieves metadata. ElevenLabs lists one voice; speech generation is not tested."
)
st.caption(
    "Gemini and Tavily checks use available account quota. No audio or video is generated."
)
if st.button(
    "Check selected APIs",
    type="primary",
    icon=":material/network_check:",
    key="check_apis",
    disabled=api.jobs.busy("api-diagnostics") or not selected,
    width="stretch",
):
    try:
        api.check_connections(selected)
    except ValueError as error:
        st.error(str(error))
    else:
        st.rerun()
activity("api-diagnostics")

with st.expander("Optional local transcription and footage generation"):
    from video_agent.inputs import transcription_config

    transcription_binary, transcription_model = transcription_config(keys)

    st.caption(
        "Editing uses uploaded assets. AI footage uses the independent creation choice and a measured local ComfyUI profile. Configure COMFY_DEVICE=cpu or cuda on the server; CPU offloading during CUDA execution is not CPU-only inference. Model installation and benchmark evidence are required separately."
    )
    st.caption(
        "whisper.cpp: "
        + (
            "executable and model available"
            if transcription_binary
            and transcription_model
            and Path(transcription_binary).is_file()
            and Path(transcription_model).is_file()
            else "unavailable; use a manual transcript or configure WHISPER_CPP_BIN and WHISPER_CPP_MODEL"
        )
    )
    if st.button("Check local footage provider", key="check_comfy"):
        from video_agent.generation import ComfyFootage

        try:
            st.json(
                ComfyFootage(
                    keys,
                    api.repository.control,
                    st.session_state.get("active_campaign") or "provider-check",
                ).readiness()
            )
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
    cid = st.session_state.get("active_campaign")
    if cid and st.button("Check campaign model access", key="check_model_access"):
        model = api.snapshot(cid)["state"]["brief"].get(
            "planning_model", "gemini-2.5-flash"
        )
        try:
            st.json(api.runtime(cid).planner.check_model_access(model))
        except (ValueError, RuntimeError) as error:
            st.error(str(error))

for provider, result in results.items():
    with st.expander(
        f"{provider} · HTTP {result['status']} · {result.get('checked_at', '')}"
    ):
        st.json(result["body"])

if "Coverr" in results:
    st.image(
        ROOT / "assets" / "coverr-logo.svg",
        width=110,
        link="https://coverr.co",
        alt="Coverr, source of the stock-video API metadata",
    )

with st.container(border=True):
    st.subheader("Production setup", icon=":material/settings_voice:")
    st.markdown(
        "**Local voice:** Kokoro on CPU, with the existing model files. No speech API key is required."
    )
    st.markdown(
        "**Optional API voice:** ElevenLabs requires TTS access and a usable voice ID. Free-tier output requires non-commercial use and attribution. [License details](https://elevenlabs.io/docs/overview/administration/billing)."
    )
    st.markdown(
        "**Optional lifestyle footage:** upload a permitted clip in the campaign brief. Image-based campaigns need no footage API. Coverr search access alone does not clear a clip for advertising use."
    )
    st.link_button(
        "Coverr API terms", "https://api.coverr.co/docs", icon=":material/open_in_new:"
    )
    import shutil

    st.caption(
        "FFprobe: "
        + (
            "available"
            if shutil.which("ffprobe")
            else "missing — add FFmpeg with FFprobe to PATH before rendering"
        )
    )
