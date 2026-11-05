"""Capture existing-media UI preview states at desktop and mobile widths.
Requires the isolated fixtures made by prepare_ui_preview.py and a running server.
Does not click generation, provider, approval-submission or export buttons.
"""

import argparse
from pathlib import Path
import json, re
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", default="http://127.0.0.1:8510")
parser.add_argument("--output", type=Path, default=Path("tmp/ui-screenshots"))
parser.add_argument("--browser-channel", default="msedge")
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=True)
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(channel=args.browser_channel, headless=True)
    for label, width, height in [
        ("desktop", 1440, 1000),
        ("mobile", 390, 844),
        ("narrow", 320, 780),
    ]:
        context = browser.new_context(
            viewport={"width": width, "height": height},
            is_mobile=width < 500,
            has_touch=width < 500,
        )
        context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if urlparse(route.request.url).hostname
                in ["127.0.0.1", "localhost", None]
                else route.abort()
            ),
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until="networkidle")
        app = page.locator("[data-testid=stApp]")

        def settled():
            expect(app).to_have_attribute(
                "data-test-script-state", "notRunning", timeout=30000
            )
            expect(page.get_by_test_id("stException")).to_have_count(0)

        settled()

        def sidebar():
            toggle = page.get_by_role("button", name="Expand sidebar", exact=True)
            if toggle.is_visible():
                toggle.click()

        def close_sidebar():
            toggle = page.get_by_role("button", name="Collapse sidebar", exact=True)
            if width < 500 and toggle.is_visible():
                toggle.click()
                page.wait_for_function(
                    '!document.querySelector("[data-testid=stSidebar]") || document.querySelector("[data-testid=stSidebar]").getBoundingClientRect().right<=0'
                )

        def select_campaign(name):
            sidebar()
            page.get_by_label("Current campaign", exact=True).click()
            page.get_by_role("option").filter(has_text=name).click()
            settled()

        def navigate(name):
            sidebar()
            page.get_by_role("link", name=name, exact=True).click()
            page.get_by_role("heading", name=name, exact=True).wait_for()
            settled()
            close_sidebar()

        def capture(name):
            settled()
            if page.locator("video").count():
                page.wait_for_function(
                    'Array.from(document.querySelectorAll("video")).every(v=>v.readyState>=2)',
                    timeout=15000,
                )
                page.locator("video").first.evaluate(
                    "async(v)=>{v.muted=true;await v.play();await new Promise(r=>v.requestVideoFrameCallback(r));v.pause();}"
                )
            page.screenshot(
                path=str(out / (label + "-" + name + ".png")), full_page=True
            )
            bounds = page.get_by_test_id("stMain").evaluate(
                "(el)=>({client:el.clientWidth,scroll:el.scrollWidth})"
            )
            results.append(
                dict(
                    viewport=label,
                    screen=name,
                    main_width=bounds,
                    js_errors=list(errors),
                )
            )
            if bounds["scroll"] > bounds["client"] + 2:
                raise RuntimeError("Horizontal overflow: " + str(results[-1]))

        sidebar()
        page.get_by_role("button", name="New campaign", exact=True).click()
        page.get_by_label("Product name", exact=True).wait_for()
        settled()
        close_sidebar()
        capture("brief")
        page.get_by_role(
            "button", name="Create campaign", exact=True
        ).scroll_into_view_if_needed()
        capture("brief-options")
        select_campaign("Plan review")
        navigate("Plan & approvals")
        capture("plan")
        select_campaign("Sample review")
        navigate("Video review")
        page.locator("video").wait_for()
        capture("sample")
        review = page.get_by_role(
            "checkbox", name="I checked the visuals, captions and audio", exact=True
        )
        review.scroll_into_view_if_needed()
        capture("sample-controls")
        expect(
            page.get_by_role("button", name="Approve sample", exact=True)
        ).to_be_disabled()
        review.focus()
        review.press("Space")
        settled()
        expect(
            page.get_by_role("button", name="Approve sample", exact=True)
        ).to_be_enabled()
        review.focus()
        review.press("Space")
        settled()
        select_campaign("Final review")
        navigate("Video review")
        page.get_by_role("radio", name=re.compile("03.*Product reveal")).click()
        page.get_by_role("heading", name="The closer look", exact=True).wait_for()
        settled()
        capture("final-review")
        select_campaign("Export ready")
        navigate("Video review")
        capture("export")
        if width < 500:
            sidebar()
            capture("sidebar")
        context.close()
    browser.close()
(out / "visual-checks.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print("Captured", len(results), "screens. All checked main viewports fit their widths.")
