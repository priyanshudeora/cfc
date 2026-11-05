# CFC marketing video studio

A local Streamlit application for planning and producing three separate 20-second, 9:16 English marketing videos for your own product: a product demonstration, a lifestyle story and a product reveal. It preserves campaign isolation, LangGraph checkpoints, SQLite persistence, local Kokoro speech and an explicit human review workflow.

**FFmpeg is the editing engine.** It performs clip selection, zoom/pan, supported masks, cuts/fades, audio mixing/ducking, caption burning and MP4 export. Pillow supplies static brand graphics; NumPy synthesizes an original instrumental bed. Image-based motion graphics are described as motion graphics, not AI-generated footage.

## Windows CMD setup

The supported application interpreter is **Python 3.14**, tested with **3.14.7**. The entry point checks this version. Local ComfyUI and whisper.cpp are optional separate installations, with their own dependencies.

```cmd
cd /d "C:\path\to\cfc"
python --version
python -m venv .venv
.venv\Scripts\activate
python -m pip install --no-compile -r requirements-app.txt
copy .env.example .env
ffprobe -version
python -m streamlit run streamlit_app.py
```

Install [FFmpeg with FFprobe](https://ffmpeg.org/download.html) and put `ffprobe.exe` on PATH. Encoding uses the FFmpeg executable provided by `imageio-ffmpeg`, including its subtitles/ASS filters. With the environment already installed, `run_app.cmd` launches the app. The default address is `http://127.0.0.1:8501`.

The **Local template** planner and **Local Kokoro** voice need no API key. Kokoro downloads its roughly 120 MB model files on an explicit generation request, then reuses them. Its Python 3.14-compatible package revision and archive checksum are pinned in `requirements-tts.txt`.

Local voice initialization calls the supported [`onnxruntime.disable_telemetry_events()` API](https://onnxruntime.ai/docs/api/python/api_summary.html#onnxruntime.disable_telemetry_events) before constructing inference sessions. Cached inference is tested with Python HTTP requests rejected. A sampled native TCP check during the local verification observed no external connections; it is not a packet capture or a guarantee about all process startup traffic. Model installation/download is separate from cached inference.

Optional API keys belong in `.env`, environment variables or Streamlit secrets. Gemini handles AI writing/revision; Tavily handles optional public-source research; ElevenLabs is an optional API voice. The app never automatically selects a paid alternative when an API/quota fails. Use **Use saved draft instead** for local recovery.

## Campaign policy is configured on the server

The campaign creation/editing form contains no usage, pricing or planning-model widgets. New campaigns use backend defaults: 64,000 model tokens, zero API speech characters, zero generated-footage jobs, 300 video-seconds of render work, zero estimated monetary allowance, no supplied prices, and `gemini-2.5-flash`. Zero speech/job allowance blocks those optional providers; the local demo still works.

An administrator can configure **new campaigns** through `.env`, environment variables or Streamlit secrets:

```dotenv
CFC_PLANNING_MODEL=gemini-2.5-flash
CFC_DEFAULT_LIMITS_JSON={"model_tokens":64000,"speech_characters":0,"generation_jobs":0,"render_seconds":300,"cost_microusd":0}
CFC_DEFAULT_USAGE_RATES_JSON={}
```

Limits merge with validated backend defaults. Prices, if supplied, are micro USD per resource unit (`model_tokens`, `speech_characters`, `generation_jobs`). They are not inferred current provider prices. The backend `CampaignService.create` also accepts explicit policy arguments. Editing an existing campaign through the form preserves its saved limits, rates and planning model even if server defaults change. Usage tracking, reservations, cost caps and scoped approval enforcement stay active.

## Reproducible local demo

1. Select **New campaign**. Enter a product name, audience and goal. Upload a main product image, optionally a second view. Add only facts you have verified, one per line. Choose Local template and Local Kokoro.
2. Review all three concepts in **Plan & approvals**. In **Timeline**, try `scene 2 move product left; quieter music` and save. A new plan review is required.
3. Inspect the sample estimate. Choose **Approve plan and save**, then **Generate representative sample**. Backend policy enforces usage limits; there are no allowance fields in the campaign form. Planning may begin without images, but missing visual assets are identified before generation is enabled.
4. Review the actual MP4, narration, contact sheet, captions and export checks. Confirm the media-review checklist and choose **Approve sample**. This authorizes only the remaining videos within the approved plan and allowance.
5. Review the selected output versions, confirm the final-review checklist and choose **Approve final videos**. Download individual MP4s, SRT captions or the ZIP bundle.
6. For a targeted revision, use Timeline on video 3. Reapprove the changed plan. Unchanged speech, scenes and videos are verified and reused; earlier versions remain available under Video review.

No user approval is supplied by page navigation, cell execution, refresh, cache lookup or restart. Scripts, facts and rendered media have distinct review responsibilities. Human review remains necessary, and advertising performance is not predicted or promised.

## Approval authorization

`control.sqlite` stores random, single-use review IDs. Each review binds the campaign, exact LangGraph checkpoint, stage, plan/version, reviewed content/file hashes, permitted actions, stage scope and usage limits. Consumption and stage-grant creation use a SQLite `BEGIN IMMEDIATE` transaction. Durable worker leases prevent concurrent campaign dispatch across service instances.

- Plan approval grants the sample scope only.
- Sample approval grants the remaining batch scope only.
- Final approval grants export of the exact reviewed output identities.

Plan hashes identify plans; they are **not** approval credentials. Media dependency keys are independent of both review IDs and campaign version numbers. Old-format saved approvals cannot silently authorize the new workflow. Approvals are separate graph nodes from rendering, and completed artifacts are verified before reuse after an interrupted processing step.

## Facts and new statements

Every scene's headline, narration, caption and footnote is checked against its selected approved facts and a small set of neutral creative phrases. Numbers written as words or digits are normalized. Spoken qualifiers such as **up to**, **total** and **including the charging case** must remain in narration; a correct footnote cannot excuse misleading speech.

CTAs use the same factual checks. Complete neutral action forms such as `Explore [product]` are permitted; arbitrary promises such as `Never charge again` require a selected approved fact or separate fact review. There is no factual-keyword blacklist shortcut. Headlines must retain complete approved factual wording, including negation, exclusions, conditions and qualifiers. `Dishwasher safe` is not authorized by `Not dishwasher safe`; uncertain shortened rewrites go to fact review rather than being accepted through word overlap.

Unmapped or uncertain statements are rejected as an active draft and saved for **separate fact review**. For example, adding `Lifetime warranty included.` does not become authorized by clicking Approve plan. The user must explicitly verify the proposed new facts, save that decision, and then approve the resulting new plan. This rule also applies to AI drafts. The checks are intentionally conservative: novel creative lines may require review too. They do not independently verify the truth of user-approved facts.

## Executable timelines and cache reuse

`SceneEdit` validates source mode (`reused`, `generated`, `hybrid`), clip boundaries, normalized placement, contain/cover, zoom, pan, mask, transition and headline layout. `AudioEdit` validates beat tempo, music level and voice level. Only supported fields compile into FFmpeg arguments; user text is never inserted as a raw filter or shell command. Prose visual/music notes explain the timeline and cannot substitute for an executable edit.

Local conversational edits include `zoom in`, `zoom out`, `no zoom`, `move product left`, `move product right`, `fade transitions`, `cut transitions`, `wipe reveal`, `remove mask`, `larger text`, `smaller text`, `no music`, `quieter music`, `louder music`, `faster beat`, `slower beat`. Prefix with `scene 2` to target a scene; separate operations with semicolons. Unsupported requests such as a fabricated camera orbit are rejected. JSON editing exposes the full validated timeline.

Scenes cache the actual visual dependencies: source hashes, selected boundaries, movement, placement, text, brand layout and rendering version. Speech caches spoken content, provider/model identity, voice settings and pronunciation separately. Caption or layout changes reuse unchanged speech; video 3 changes preserve video 1/2 dependencies. Cache manifests contain artifact hashes, and verifiers check cached media before reuse. Corrupted artifacts are rebuilt within retry and usage limits.

## Multimodal input

Campaign brief accepts text context, reference images/video, uploaded speech or microphone recording. Analysis is cached by file hash and analyzer/model identity, then saved into the brief. Video analysis extracts four frames and an optional transcript, independently of the lifestyle-footage selector. Reference input does not automatically approve claims or become final advertising footage.

Image context is sent at initial AI planning at bounded resolution; successful visual context and the saved plan are reused on revisions. Reference-video frames are extracted locally. First-frame/colour/metadata analysis is not a claim of semantic understanding; optional Gemini planning interprets the available visual context.

For local transcription, install [whisper.cpp](https://github.com/ggml-org/whisper.cpp/tree/master/examples/cli), obtain a compatible model, and set `WHISPER_CPP_BIN` and `WHISPER_CPP_MODEL`. The adapter invokes `whisper-cli -oj -of` on FFmpeg-normalized mono audio. No executable/model is bundled. If unavailable, the app reports this and accepts a manual transcript; it does not call a paid transcription provider. Reference audio/video is limited to three minutes for analysis.

The development machine additionally has an ignored local install of whisper.cpp **v1.9.2** and `ggml-tiny.en.bin` (English, about 78 MB). It was checked with synthetic Kokoro speech and produced the expected transcript. The app can discover its local `data/tools/whisper/install.json`; explicit environment settings take precedence. Fresh clones still require their own installation. Tiny English is a demo model: review transcriptions, and configure a multilingual model for other languages.

For an explicit Windows x64 setup using Python's standard library:

```cmd
python scripts\setup_whisper.py
```

The helper downloads about 86 MB from the official whisper.cpp release and its model repository, verifies pinned SHA-256 hashes, checks safe extraction paths and starts `whisper-cli --help`. It is never run automatically. A fresh destination was installed and used to transcribe the real exported Kokoro videos on 2026-11-05. **No real user recording was supplied**, so that verification remains pending; synthetic speech is not represented as a user recording. A missing installation reports the manual-transcript fallback, which is covered by a regression test.

## Optional new footage generation

The real optional adapter targets a **local ComfyUI server**, not a cloud billing endpoint. Its [official HTTP interface](https://docs.comfy.org/development/comfyui-server/comms_routes) provides `/prompt`, `/queue`, `/history`, `/object_info`, `/system_stats` and `/view`.

1. Install ComfyUI and the chosen local video model separately. As one model example, [Wan2.1's official repository](https://github.com/Wan-Video/Wan2.1) lists an 8.19 GB VRAM requirement for T2V-1.3B, before workflow-specific overhead; this does not guarantee that every workflow fits or runs quickly.
2. Export and test a local-only **API-format workflow** producing MP4/WebM/MOV. Use `__PROMPT__` and `__SEED__` placeholders. Only the adapter's explicit local-node allowlist is accepted; partner/cloud API nodes are unsupported.
3. Set `COMFY_URL`, `COMFY_WORKFLOW` and `COMFY_MIN_VRAM_GB` in `.env`. The default declared VRAM threshold is 10 GB. Use **API connections → Check local footage provider** to inspect model/node access and device statistics.
4. Have the server policy provide a nonzero generation-job allowance, then select generated or hybrid mode in the structured timeline and supply an ambient-footage prompt. Save/reapprove the timeline. Hybrid composites the exact uploaded product image over generated footage; it is not image-to-video conditioning. The current adapter has no image-upload/loader binding into the model.

The adapter persists client/provider job identifiers before/after submission, polls/downloads with ordinary code, and reconciles queue/history after ambiguous outcomes. It does not submit another job merely because a response was lost. Workflow identity is pinned in the approved brief. Model weights, GPU capacity and output quality are not supplied by this repository. **No live ComfyUI model is connected or tested on the development machine**; protocol behavior is tested with controlled responses. The supported fallback is explicit reused-asset motion graphics.

## Background work, usage and recovery

Scene-level SQLite task records include content key, kind, state, attempt count, provider ID, output and errors. Worker/resource leases have heartbeats. Independent scene tasks run concurrently within configurable limits:

```cmd
set CFC_CPU_WORKERS=2
set CFC_GPU_WORKERS=1
set CFC_PROVIDER_WORKERS=1
```

These control simultaneous tasks, not physical CPU cores or GPU memory allocation. Scene FFmpeg processes also use bounded thread counts.

The activity panel persists progress and supports **Stop queued and future work**. The local ComfyUI adapter deletes owned pending jobs where possible; already-running provider work may finish. It never issues an indiscriminate server-wide interrupt. After restart, the worker lease must expire before recovery becomes available. Use **Retry interrupted step**; verified caches avoid repeating completed work. Unknown synchronous API outcomes stay blocked until the user checks account/provider usage and explicitly reconciles them. Known ComfyUI jobs are polled/downloaded instead of resubmitted.

Allowances track model tokens, API speech characters, local footage jobs, estimated video-second rendering work, and currency estimates separately. Reservations precede dispatch; successful usage is reconciled afterward. Unknown requests retain their reservation. Retry counts are bounded. Exceeding a limit pauses work rather than selecting another provider. Model IDs are configurable and account model access is checked before AI planning.

Gemini usage is settled as soon as a response is received, even if model text is empty, truncated, malformed or schema-invalid. Such tasks become `validation_failed` (or `response_failed` for a known HTTP rejection), with per-attempt usage/error records under `model_responses/`. An explicit retry may submit at most twice for the same request, reserves remaining allowance first, and reuses valid parsed responses. Missing usage is conservatively estimated and labelled. A genuinely ambiguous network failure stays `unknown` with its reservation retained until reconciliation; it is not automatically retried.

Currency estimates use **user-supplied unit prices**, not invented current API prices. With rates absent, money is shown as unpriced; token/character/job caps still apply. Local compute/electricity costs are not estimated. These are application usage controls, not a guarantee about a provider's invoice, free-tier availability or account billing settings. ElevenLabs free-tier licensing/account limits must be checked for the intended use; see its [official billing documentation](https://elevenlabs.io/docs/overview/administration/billing).

## Research and output verification

Public-source research records URLs, observation times, available publication dates, source kind, positioning/hook excerpts and observed CTA text. Organic posts/product pages are distinguished from unverified public pages. No source is labelled an active or verified advertisement without evidence, and no campaign performance is invented. Results are deduplicated and expire after 24 hours. **Refresh public competitor sources** bypasses the cache and returns to plan review.

Technical validation explicitly checks duration, dimensions, frame count, H.264/AAC formats, audio levels and complete decoding. Production checks use explicit exceptions, not assertions. A rendered-media contract checks exact executable timeline correspondence, source integrity and narration/caption events. A contact sheet is extracted from the actual export. Human review evaluates semantic product appearance, phone readability, pronunciation and tone; this is separate from script review.

## Files and modules

`data/studio/campaigns/<id>/` holds the brief, immutable uploaded file versions, analysis, approved plans, caches, prior output versions, reconciliation records and deliverables. `checkpoints.sqlite` holds LangGraph state; `control.sqlite` holds reviews/tasks/usage/leases. Set `CFC_DATA_DIR` to choose another data directory.

Core modules: `control.py` (authorization/tasks/usage), `service.py` (validated actions), `workflow.py` (LangGraph), `facts.py` (copy checks), `timeline.py` (edit schema), `cache.py` (verified reuse), `inputs.py` (analysis), `generation.py` (optional local queue), `planning.py` (Gemini/research), and `rendering/editor.py` / `rendering/review.py` (FFmpeg and rendered-media checks). `app_pages/` preserves the Streamlit screens, including an architecture view of implemented and optional components.

The repository excludes `.env`, uploaded product assets, campaigns, local model weights, generated videos, caches and earlier notebook experiments. Fresh clones start empty. API diagnostics use small explicit requests; normal tests do not consume live credits.

## Tests

```cmd
.venv\Scripts\activate
python -m pip install --no-compile -r requirements-dev.txt
python -m pytest tests -q
python -m pytest tests/test_ffmpeg_editor.py -q
```

The FFmpeg smoke test creates synthetic images/audio, burns captions, exports a real MP4, decodes it, checks effective visual edits and verifies unchanged-scene/speech reuse. Other regressions cover cross-stage/stale/duplicate approvals, unknown facts, spelled quantities, budgets, input-analysis caching, research refresh and restart/unknown-submission recovery. Tests use temporary campaign data, not excluded notebook fixtures or live credits. The optional local Kokoro integration check skips when model files are absent.

For the complete local workflow with **explicitly simulated test approvals**, cached real Kokoro models, FFmpeg, original music and three actual MP4 exports:

```cmd
python scripts\verify_local_demo.py --simulate-approvals
```

This creates an isolated campaign under `tmp/`, runs plan/sample/final checkpoints, changes video 3, reapproves the changed test plan and exports again. It checks byte-identical reuse of videos 1/2, changed video 3, speech reuse and non-silent narration/music. Python HTTP requests are rejected during this run, so it cannot spend live API credits or download models. Optional `--main-image`, `--detail-image`, `--product`, `--brand` and repeated `--fact` arguments use your own supplied inputs. Test approvals do not approve a real campaign for publishing.

The recorded 2026-11-05 run used supplied product images, passed both three-video export rounds, and transcribed the actual mixed MP4 audio using the fresh whisper.cpp install. Evidence is local at `tmp/verified-demo-20261105/demo_report.json`, `fresh_transcription_report.json`, the MP4s and contact sheets. A fresh clone should rerun the script because generated artifacts are excluded from Git. The eight-task status table is on the Architecture screen, with source data in [docs/verification-status.json](docs/verification-status.json). Actual ComfyUI inference, real user-recording transcription, semantic visual assessment and word-level alignment are not claimed as verified.

## Future work

External editing-application MCP integration and **Jev** integration are future work. Neither is connected, and ordinary local Python functions are not described as MCP or Jev. Other remaining limitations include automated semantic product-appearance review, word-level forced-alignment captions, automatic model/workflow installation, and provider invoice reconciliation. Uploaded product images plus human media review remain the reliable local demo path.
