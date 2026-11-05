"""Integration checks for unrelated products, isolated assets and renewed approvals."""

import copy
import json
from pathlib import Path
import pytest
from video_agent.models import CampaignPlan
from video_agent.validation import plan_copy_issues
from video_agent.service import CampaignService
from video_agent.config import Settings
from test_workflow import (
    studio as studio,
    make_campaign,
    product_upload,
    perform,
    stub_runtime,
)


def test_two_unrelated_products_have_isolated_facts_images_and_branding(studio):
    first = make_campaign(
        studio,
        product="Daily Roast",
        brand="Morning Club",
        claims=["250 g roasted coffee."],
        product_image=product_upload("red"),
    )
    second = make_campaign(
        studio,
        product="Trail Pack",
        brand="Hill Company",
        claims=["18 L capacity."],
        product_image=product_upload("green", (800, 220)),
    )
    for cid, own, other in [
        (first, "Daily Roast", "Trail Pack"),
        (second, "Trail Pack", "Daily Roast"),
    ]:
        state = studio.snapshot(cid)["state"]
        encoded = json.dumps(state)
        assert own in encoded and other not in encoded
        assert not any(
            term in encoded.lower()
            for term in ["boat", "airdopes", "ipx4", "battery_42", "75 minutes"]
        )
        assert state["brief"]["cta"] == f"Explore {own}."
        assert not state["research"]
        assets = studio.repository.assets(cid)
        assert all(
            str(studio.repository.folder(cid)) in a["path"] for a in assets.values()
        )
        renderer = studio.runtime(cid).renderer
        from PIL import Image, ImageDraw

        photos = renderer.identity_images()
        assert photos["product_main"].size in [(360, 600), (800, 220)]
        frame = Image.new("RGB", (1080, 1920), "white")
        texts, original = [], renderer.draw_text

        def capture(draw, text, *args, **kwargs):
            texts.append(text)
            return original(draw, text, *args, **kwargs)

        renderer.draw_text = capture
        try:
            renderer.draw_identity(ImageDraw.Draw(frame))
            for ad in state["plan"]["ads"]:
                renderer.draw_text(
                    ImageDraw.Draw(frame),
                    ad["scenes"][-1]["headline"],
                    (84, 314),
                    912,
                    234,
                    72,
                    "black",
                    True,
                )
            assert state["brief"]["brand"] in texts
            assert own in texts and state["brief"]["cta"] in texts
            assert not any("boAt" in text or "AIRDOPES" in text for text in texts)
        finally:
            renderer.draw_text = original
    assert (
        studio.repository.assets(first)["product_main"]["sha256"]
        != studio.repository.assets(second)["product_main"]["sha256"]
    )


def test_new_campaign_works_without_bundled_example_assets(tmp_path):
    api = CampaignService(
        Settings(data_dir=tmp_path / "studio", reference_dir=tmp_path / "absent"),
        import_notebook=False,
    )
    try:
        cid = make_campaign(api, claims=[])
        snap = api.snapshot(cid)
        assert not snap["state"]["brief"]["approved_claims"]
        assert snap["stage"] == "approve_plan"
        _, rendered = stub_runtime(api, cid)
        for action in ["approve", "generate", "approve", "approve"]:
            snap = perform(api, cid, action)
        assert rendered == ["product_demo", "lifestyle_story", "product_reveal"]
        assert snap["state"]["status"] == "complete"
    finally:
        api.close()


def test_product_facts_accept_their_own_numbers_and_reject_another_campaign(studio):
    cid = make_campaign(
        studio, product="Desk Lamp", claims=["Up to 100 hours total runtime."]
    )
    state = studio.snapshot(cid)["state"]
    assert not plan_copy_issues(
        CampaignPlan.model_validate(state["plan"]), state["brief"]
    )
    changed = copy.deepcopy(state["plan"])
    changed["ads"][0]["scenes"][1]["narration"] = "100 hours runtime."
    changed["ads"][0]["scenes"][1]["caption"] = "100 hours runtime."
    issues = plan_copy_issues(CampaignPlan.model_validate(changed), state["brief"])
    assert any("qualifier" in issue for issue in issues)
    changed["ads"][0]["scenes"][1]["headline"] = "42 hours playback"
    assert any(
        "42" in issue
        for issue in plan_copy_issues(
            CampaignPlan.model_validate(changed), state["brief"]
        )
    )


def test_brief_changes_reset_approval_preview_and_scripts(studio):
    cid = make_campaign(studio)
    stub_runtime(studio, cid)
    perform(studio, cid, "approve")
    before = perform(studio, cid, "generate")
    old_video = Path(before["state"]["sample"]["video"])
    studio.update_brief(
        cid,
        {
            "product": "Trail Bottle Plus",
            "claims": ["1 L capacity."],
            "cta": "Meet the new bottle.",
            "accent_color": "#DD3355",
        },
        before["signature"],
    )
    after = studio.snapshot(cid)
    assert after["stage"] == "approve_plan" and not after["approved"]
    assert after["state"]["version"] == before["state"]["version"] + 1
    assert not after["state"]["sample"] and not after["state"]["outputs"]
    assert old_video.is_file()
    assert all(
        ad["scenes"][-1]["narration"] == "Meet the new bottle."
        for ad in after["state"]["plan"]["ads"]
    )
    assert "750" not in json.dumps(after["state"]["plan"])
    with pytest.raises(ValueError, match="changed"):
        studio.act(cid, "approve", before["signature"])


def test_bad_or_missing_images_do_not_create_campaigns(studio):
    for upload in [("fake.png", b"not an image")]:
        with pytest.raises(ValueError):
            make_campaign(studio, product_image=upload)
    assert not studio.repository.list()
    cid = make_campaign(studio)
    asset = studio.repository.assets(cid)["product_main"]
    Path(asset["path"]).write_bytes(b"changed image")
    with pytest.raises(ValueError, match="changed"):
        studio.repository.assets(cid)
