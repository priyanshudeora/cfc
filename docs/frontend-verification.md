# Frontend update verification

This records the earlier frontend-only phase. The later genuine-generation implementation, real inference measurements and additional browser checks are documented in [local-generation.md](local-generation.md); its changes extend the backend as well as the interface. The 83-test count below is the historical frontend-phase result.

Verified on 5 November 2026 with Python 3.14.7 and Streamlit 1.66.1. Changes are limited to presentation, navigation, UI action bindings and QA helpers. Campaign storage, LangGraph execution, provider adapters, usage policy, caches, media rendering and export implementation were preserved.

The shared design lives in `.streamlit/config.toml`: a neutral light background, restrained teal accent, smaller headings, readable 15 px base text and reduced page padding. No custom CSS or generated-class selectors were introduced. Native controls retain labels, keyboard interaction and responsive stacking.

## Automated tests

`python -m pytest tests -q`: **83 passed**, with 13 existing third-party LangSmith deprecation warnings. This includes campaign creation/editing, preservation of hidden settings, plan/sample/final approvals, revisions, exports, authorization, usage and real FFmpeg checks. New UI coverage verifies provider-choice bindings without calling providers, sample/final approval checklists, switching videos, selected-video revision routing, missing MP4/caption approval blocking and the saved-stage workflow indicator.

AppTest uses isolated temporary data and controlled providers/renderers for interaction tests. These tests establish behavior; they are not screenshots or proof of visual appearance. The existing FFmpeg integration tests separately encode and decode real media.

## Browser and visual inspection

The connected browser host was unavailable. A locally installed Microsoft Edge 156 was launched headlessly through Playwright 1.56.0 instead. The actual running Streamlit interface was rendered at **1440 × 1000**, **390 × 844** and **320 × 780**. These are browser viewport checks, not physical-phone tests.

The preview used four explicitly labelled, isolated campaigns under `tmp/ui-preview`, importing already verified local MP4s and product images. No new media was generated for UI inspection. Preview setup is a test fixture, not human approval of a real advertising campaign, and it never writes to production campaigns. The imported media is checked against the template timeline before use.

The browser run captured 23 screens covering campaign creation, optional sections, plan cards, sample controls, final review, export and mobile navigation. It checked for JavaScript exceptions and horizontal main-content overflow. Keyboard Space toggled the review checkbox and enabled/disabled sample approval without submitting it. Switching to video 3 was verified through the actual selected title. Native video playback was started muted and paused after a decoded frame before screenshots. Screenshots and machine-readable results are local at `tmp/ui-screenshots/`; JavaScript exceptions and horizontal overflow were absent in the checked views. The isolated database recorded **zero provider tasks and zero usage reservations**.

Visual inspection confirmed the two-column desktop brief, stacked mobile fields, comparable concept cards, portrait player beside desktop controls, controls below the player on mobile, wrapping video choices at 320 px, and the final campaign download. Initial captures taken during a sidebar transition were replaced after waiting for it to close.

## Reproduce with existing media

In CMD, use the project's activated environment. The report must come from an unmodified local-template run of `scripts/verify_local_demo.py`; `--output` must be a fresh directory. Existing files are never deleted.

```cmd
python -m pip install -r requirements-ui-test.txt
python scripts\prepare_ui_preview.py --report tmp\local-demo-8337413c\demo_report.json --output tmp\ui-preview-new
set CFC_DATA_DIR=%CD%\tmp\ui-preview-new
python -m streamlit run streamlit_app.py --server.port 8510
```

In a second CMD window, with the same environment activated:

```cmd
python scripts\capture_frontend.py --url http://127.0.0.1:8510 --output tmp\ui-screenshots
```

Replace the report path with your existing local verification report. If none exists, the README documents how to create a local demo without paid providers. The capture helper expects the four `UI preview` campaigns created by the preparation script. It never clicks generation, provider requests, approval submissions or export actions.

For the normal application, leave `CFC_DATA_DIR` unset and launch `python -m streamlit run streamlit_app.py` as usual.

## Limits

Physical iOS/Android browsers, Safari and assistive technology were not exercised. Native media-control details vary by browser. This is not a formal WCAG audit. Backend paid-provider calls were deliberately not made, and no new generation quality claim is made by this frontend update.
