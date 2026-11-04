"""Native Streamlit AppTest coverage; no server or paid providers are started."""

from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
from video_agent.ui import get_service

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CFC_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("Unexpected network request while rendering a page")
        ),
    )
    get_service.clear()
    api = get_service(str(tmp_path))
    from test_workflow import make_campaign, stub_runtime, perform

    cid = make_campaign(api)
    stub_runtime(api, cid)
    perform(api, cid, "approve")
    perform(api, cid, "generate")
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=40).run()
    assert not at.exception
    yield at, api
    api.close()
    get_service.clear()


def test_pages_render_without_generating_media(app):
    at, api = app
    cid = at.session_state["active_campaign"]
    before = api.snapshot(cid)
    assert before["stage"] == "approve_sample"
    assert any("remaining two videos" in item.value for item in at.warning)
    for page in ["brief", "plan", "videos", "connections", "architecture", "overview"]:
        at.switch_page(f"app_pages/{page}.py").run()
        assert not at.exception, (page, at.exception)
    after = api.snapshot(cid)
    assert (
        after["signature"] == before["signature"] and after["stage"] == before["stage"]
    )
    assert after["job"] == before["job"]


def test_plan_approval_and_manual_revision_ui(app):
    at, api = app
    cid = at.session_state["active_campaign"]
    at.switch_page("app_pages/plan.py").run()
    before = api.snapshot(cid)
    at.button(key="reopen_plan_" + before["signature"]).click().run()
    api.jobs.wait(cid)
    at.run()
    assert not at.exception
    snap = api.snapshot(cid)
    assert snap["stage"] == "approve_plan"
    at.button(key="approve_plan_" + snap["signature"]).click().run()
    api.jobs.wait(cid)
    at.run()
    assert not at.exception
    snap = api.snapshot(cid)
    assert snap["stage"] == "ready_sample" and snap["approved"]
    assert not at.button(key="generate_" + snap["signature"]).disabled
    at.segmented_control(key="plan_detail_" + cid).set_value("Edit script").run()
    assert not at.exception
    headline = next(
        w for w in at.text_input if w.label == "Headline" and not w.disabled
    )
    headline.set_value("Five minutes. Big plans.")
    next(
        w for w in at.form_submit_button if w.label == "Save revised draft"
    ).click().run()
    assert not at.exception
    after = api.snapshot(cid)
    assert after["stage"] == "approve_plan" and not after["approved"]
    assert not after["state"].get("sample") and not after["state"].get("outputs")


def test_create_campaign_validates_required_product_inputs(app):
    at, api = app
    count = len(api.repository.list())
    at.button(key="new_campaign_button").click().run()
    assert not at.exception
    assert at.text_input(key="product_name").value == ""
    assert at.text_input(key="brand_name").value == ""
    at.text_input(key="campaign_name").set_value("Campus launch test")
    at.text_input(key="campaign_audience").set_value("College students")
    next(w for w in at.form_submit_button if w.label == "Create campaign").click().run()
    assert not at.exception
    assert len(api.repository.list()) == count
    assert any("product name" in item.value for item in at.error)


def test_empty_plan_feedback_is_rejected_in_ui(app):
    at, api = app
    cid = at.session_state["active_campaign"]
    at.switch_page("app_pages/plan.py").run()
    at.segmented_control(key="plan_detail_" + cid).set_value("Request changes").run()
    before = api.snapshot(cid)
    next(
        w for w in at.form_submit_button if w.label == "Request revised plan"
    ).click().run()
    assert not at.exception
    assert any("Describe" in error.value for error in at.error)
    assert api.snapshot(cid)["signature"] == before["signature"]


def test_connection_response_and_attribution_render(app):
    at, api = app
    from video_agent.storage import save_json

    save_json(
        api.settings.data_dir / "api_checks.json",
        {
            "Coverr": dict(
                status=200,
                ok=True,
                checked_at="Saved test run",
                body={"hits": [{"title": "Metadata test result"}]},
            )
        },
    )
    at.switch_page("app_pages/connections.py").run()
    assert not at.exception
    assert any("HTTP 200" in item.value for item in at.caption)


def test_general_product_pages_and_brief_edit_ui(app):
    from test_workflow import make_campaign

    at, api = app
    cid = make_campaign(
        api, product="Travel Mug", brand="North", claims=["350 ml capacity."]
    )
    at.run()
    at.selectbox(key="active_campaign").select(cid).run()
    for page in ["overview", "brief", "plan", "videos", "architecture"]:
        at.switch_page(f"app_pages/{page}.py").run()
        assert not at.exception, (page, at.exception)
    at.switch_page("app_pages/brief.py").run()
    assert any("Travel Mug" in item.value for item in at.subheader)
    assert not any("boAt" in item.value for item in at.markdown)
    at.button(key="edit_brief").click().run()
    assert not at.exception
    assert at.text_input(key="product_name").value == "Travel Mug"
    at.text_input(key="campaign_cta").set_value("Discover Travel Mug.")
    next(
        w for w in at.form_submit_button if w.label == "Save brief and reset plan"
    ).click().run()
    assert not at.exception
    snap = api.snapshot(cid)
    assert snap["stage"] == "approve_plan" and not snap["approved"]
    assert snap["state"]["brief"]["cta"] == "Discover Travel Mug."
    at.switch_page("app_pages/plan.py").run()
    at.segmented_control(key="plan_detail_" + cid).set_value("Edit script").run()
    assert not at.exception
    assert len(at.multiselect) == 5


def test_example_is_hidden_and_stale_session_cannot_reopen_it(tmp_path, monkeypatch):
    from video_agent.config import LEGACY_ID

    monkeypatch.setenv("CFC_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "video_agent.service.CampaignService.import_existing",
        lambda self: (_ for _ in ()).throw(AssertionError("Automatic example import")),
    )
    get_service.clear()
    api = get_service(str(tmp_path))
    try:
        assert not api.repository.list()
        api.repository.register(LEGACY_ID, "boAt Airdopes 141 · Black", imported=True)
        original = (api.repository.folder(LEGACY_ID) / "project.json").read_bytes()
        at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=40).run()
        assert not at.exception
        assert not api.list_campaigns()
        assert any("Your next campaign" in item.value for item in at.subheader)
        at.session_state["active_campaign"] = LEGACY_ID
        at.session_state["pending_campaign"] = LEGACY_ID
        at.session_state["edit_campaign"] = LEGACY_ID
        at.run()
        assert at.session_state["active_campaign"] is None
        for page in [
            "overview",
            "brief",
            "plan",
            "videos",
            "connections",
            "architecture",
        ]:
            at.switch_page(f"app_pages/{page}.py").run()
            assert not at.exception, (page, at.exception)
            assert not any(widget.key == "active_campaign" for widget in at.selectbox)
            texts = [
                item.value
                for kind in [at.markdown, at.caption, at.subheader, at.info, at.title]
                for item in kind
            ]
            assert not any(
                "boat" in text.lower() or "airdopes" in text.lower() for text in texts
            )
            assert not at.get("video")
        assert (
            api.repository.folder(LEGACY_ID) / "project.json"
        ).read_bytes() == original
    finally:
        api.close()
        get_service.clear()
