import streamlit as st
from video_agent.ui import service, footage_upload, input_panel
from video_agent.generation_contract import CREATION_CHOICES

api = service()
cid = st.session_state.get("active_campaign")
new = st.session_state.get("new_campaign") or not cid
snapshot = api.snapshot(cid) if cid else None
brief = snapshot["state"].get("brief", {}) if snapshot else {}
editing = not new and st.session_state.get("edit_campaign") == cid

if not new and not editing:
    st.markdown(
        "Your product images and approved facts stay with this campaign. Every changed brief needs a new plan approval."
    )
    left, right = st.columns([1.5, 1])
    with left:
        with st.container(border=True):
            st.subheader(brief.get("product", "Product campaign"))
            st.caption(
                " · ".join(filter(None, [brief.get("brand"), brief.get("variant")]))
            )
            st.markdown("**Audience**  \n" + brief.get("audience", ""))
            st.markdown("**Goal**  \n" + brief.get("goal", ""))
            st.markdown("**Ending call to action**  \n" + brief.get("cta", ""))
            st.caption(
                "Creation: " + CREATION_CHOICES[brief.get("creation_mode", "edit")]
            )
            st.caption(
                "Three 20-second vertical videos · English narration and captions"
            )
            st.markdown("**Approved product facts**")
            for key, claim in brief.get("approved_claims", {}).items():
                st.markdown(":material/check_circle: " + claim)
                st.caption(key)
            if not brief.get("approved_claims"):
                st.caption(
                    "No feature claims supplied. The campaign uses product appearance and your message."
                )
        if brief.get("schema_version", 1) >= 2:
            if st.button(
                "Edit campaign brief",
                key="edit_brief",
                disabled=snapshot["busy"],
                icon=":material/edit:",
            ):
                st.session_state["edit_campaign"] = cid
                st.rerun()
        if st.button("Create another campaign", type="primary", key="create_another"):
            st.session_state["new_campaign"] = True
            st.rerun()
    with right:
        identity = [
            a for a in api.repository.assets(cid).values() if a["use"] == "identity"
        ]
        if identity:
            st.image(
                identity[0]["path"],
                width=340,
                alt="Supplied product image for "
                + brief.get("product", "this campaign"),
            )
    footage_upload(api, cid, snapshot)
    input_panel(api, cid, snapshot)
else:
    values = brief if editing else {}
    st.markdown(
        "Create marketing videos for your product. Add its images, audience and approved facts; review the plan before creating media."
    )
    if editing:
        st.info(
            "Saving creates a new plan that needs approval again. Previous video files are retained."
        )
    with st.form("edit_campaign_brief" if editing else "create_campaign", border=True):
        details, assets = st.columns([1.45, 1], gap="large")
        with details:
            st.markdown("**Product & message**")
            if not editing:
                name = st.text_input(
                    "Campaign name",
                    max_chars=100,
                    key="campaign_name",
                    placeholder="Summer product launch",
                )
            product_col, brand_col = st.columns(2)
            product = product_col.text_input(
                "Product name",
                value=values.get("product", ""),
                max_chars=80,
                key="product_name",
                placeholder="Exact product name",
            )
            brand = brand_col.text_input(
                "Brand (optional)",
                value=values.get("brand", ""),
                max_chars=80,
                key="brand_name",
            )
            audience = st.text_input(
                "Audience",
                value=values.get("audience", ""),
                max_chars=300,
                key="campaign_audience",
                placeholder="Who should this campaign reach?",
            )
            goal = st.text_area(
                "Campaign goal",
                value=values.get(
                    "goal",
                    "Attract attention and encourage viewers to explore the product.",
                ),
                height=80,
                max_chars=800,
                key="campaign_goal",
            )
            claims = st.text_area(
                "Approved product facts \u00b7 one per line (optional)",
                value="\n".join(values.get("approved_claims", {}).values()),
                height=110,
                key="campaign_claims",
                help="Only verified facts can appear as product claims. Preserve qualifiers such as 'up to'. Up to 12 facts, 160 characters each.",
            )
            cta = st.text_input(
                "Ending call to action",
                value=values.get("cta", ""),
                max_chars=90,
                key="campaign_cta",
                placeholder="Explore [product name].",
            )
        with assets:
            st.markdown("**Product images**")
            st.caption(
                "These images appear in the videos. Use clear views of the exact product."
            )
            if not editing:
                main = st.file_uploader(
                    "Main product image",
                    type=["png", "jpg", "jpeg", "webp"],
                    key="main_product_image",
                )
                detail = st.file_uploader(
                    "Second angle or detail (optional)",
                    type=["png", "jpg", "jpeg", "webp"],
                    key="detail_product_image",
                )
                st.caption(
                    "You can plan without images; generation waits for the required assets."
                )
            else:
                images = [
                    a
                    for a in api.repository.assets(cid).values()
                    if a["use"] == "identity"
                ]
                if images:
                    st.image(
                        images[0]["path"],
                        width=240,
                        alt="Saved product image for "
                        + values.get("product", "this campaign"),
                    )
                st.caption(
                    "Saved images stay with this campaign. Replace them from the saved brief view."
                )
        with assets:
            creation_mode = st.selectbox(
                "Creation",
                list(CREATION_CHOICES),
                index=list(CREATION_CHOICES).index(values.get("creation_mode", "edit")),
                format_func=lambda key: CREATION_CHOICES[key],
                key="creation_mode",
            )
            st.caption(
                "Planning writes the scripts. Creation chooses the media source. New AI footage uses a separately configured local ComfyUI model and your product image as conditioning."
            )
            st.caption(
                "AI footage is experimental and can change product details. Review its sample. Short generated clips are looped to the approved scene duration; no automatic editing-only fallback."
            )
        if not editing:
            with assets:
                starting = st.segmented_control(
                    "Planning",
                    ["Local draft", "AI concepts"],
                    default="Local draft",
                    key="starting_point",
                )
                st.caption(
                    "Local draft uses no API. AI concepts use Gemini account quota. Both need approval."
                )
                voice = st.segmented_control(
                    "Voice",
                    ["Local voice", "ElevenLabs"],
                    default="Local voice",
                    key="voice_provider",
                )
                st.caption(
                    "Local voice runs here. ElevenLabs uses account credits and the server's allowance."
                )
                with st.expander("ElevenLabs voice details"):
                    voice_id = st.text_input(
                        "ElevenLabs voice ID", max_chars=100, key="elevenlabs_voice"
                    )
                    st.caption(
                        "Only required for ElevenLabs. Check your account's license for the intended use."
                    )
        with st.expander("Creative direction (optional)"):
            style = st.text_input(
                "Creative style",
                value=values.get("style", "Energetic, clear and realistic"),
                max_chars=300,
                key="campaign_style",
            )
            variant = st.text_input(
                "Model, colour or variant",
                value=values.get("variant", ""),
                max_chars=100,
                key="product_variant",
            )
            product_url = st.text_input(
                "Official product page",
                value=values.get("product_url", ""),
                key="product_url",
                help="Planning context only. Page content does not automatically authorize product claims.",
            )
            accent = st.color_picker(
                "Brand accent colour",
                value=values.get("accent_color", "#00AABD"),
                key="brand_accent",
            )
        with st.expander("Competitor research (optional)"):
            competitors = st.text_area(
                "Comparable products \u00b7 one per line",
                value="\n".join(values.get("competitors", [])),
                height=80,
                key="campaign_competitors",
            )
            if not editing:
                research = st.checkbox(
                    "Research these competitors", value=False, key="refresh_research"
                )
            st.caption(
                "Up to three products. Research uses Tavily account quota and provides context, not approved claims."
            )
        if not editing:
            with st.expander("Reference images (optional)"):
                refs = st.file_uploader(
                    "Style references \u00b7 up to five images",
                    type=["png", "jpg", "jpeg", "webp"],
                    accept_multiple_files=True,
                    key="style_reference_images",
                )
                st.caption(
                    "References guide planning. They are not used as final video footage. Add reference video or a spoken brief after saving the campaign."
                )
        with st.expander("Lifestyle footage (optional)"):
            use_footage = st.checkbox(
                "Use my own lifestyle footage in the story video",
                value=values.get("use_footage", False),
                key="use_lifestyle_footage",
            )
            st.caption(
                "If enabled, you must upload the planned licensed clips in Campaign brief after saving. The story video cannot render until its footage is available. Reference media does not satisfy this requirement."
            )
        st.caption(
            "3 portrait videos \u00b7 20 seconds each \u00b7 English voice, music and captions. Media generation starts only after plan approval."
        )
        submitted = st.form_submit_button(
            "Save brief and reset plan" if editing else "Create campaign",
            type="primary",
            icon=":material/arrow_forward:",
            width="content",
            disabled=bool(snapshot and snapshot["busy"]),
        )
    if submitted:
        try:
            changes = dict(
                product=product,
                brand=brand,
                variant=variant,
                audience=audience,
                goal=goal,
                claims=claims,
                cta=cta,
                style=style,
                product_url=product_url,
                competitors=competitors,
                accent_color=accent,
                use_footage=use_footage,
                creation_mode=creation_mode,
            )
            if editing:
                api.update_brief(cid, changes, snapshot["signature"])
                new_id = cid
            else:
                new_id = api.create(
                    name,
                    audience,
                    goal,
                    "ai" if starting == "AI concepts" else "template",
                    "elevenlabs" if voice == "ElevenLabs" else "local_kokoro_notebook",
                    voice_id,
                    research,
                    **{
                        k: v
                        for k, v in changes.items()
                        if k not in ["audience", "goal"]
                    },
                    product_image=(main.name, main.getvalue()) if main else None,
                    detail_image=(detail.name, detail.getvalue()) if detail else None,
                    style_images=[
                        (image.name, image.getvalue()) for image in refs or []
                    ],
                )
        except (ValueError, RuntimeError) as error:
            st.error(str(error), title="Check your campaign details")
        else:
            st.session_state["pending_campaign"] = new_id
            st.session_state["new_campaign"] = False
            st.session_state.pop("edit_campaign", None)
            st.switch_page("app_pages/plan.py")
    if editing and st.button("Cancel brief edit", key="cancel_brief_edit"):
        st.session_state.pop("edit_campaign", None)
        st.rerun()
    if (
        not editing
        and cid
        and st.button("Cancel new campaign", key="cancel_new_campaign")
    ):
        st.session_state["new_campaign"] = False
        st.switch_page("app_pages/overview.py")
