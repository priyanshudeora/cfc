# CFC video studio

A modular Streamlit application for marketing **any product** using your own images and approved facts. Each campaign creates three distinct 20-second vertical videos: a product demonstration, a lifestyle story and a product reveal, with English voiceover, original music, captions and your chosen call to action.

## Run in CMD

Use **Python 3.14** (developed with 3.14.7). After cloning the repository, open CMD in its folder:

```cmd
python --version
python -m venv .venv
.venv\Scripts\activate
python -m pip install --no-compile -r requirements-app.txt
copy .env.example .env
python -m streamlit run streamlit_app.py
```

You can also double-click **`run_app.cmd`** after dependencies are installed. Streamlit opens the browser locally; the default address is `http://localhost:8501`.

Add optional API keys to your local `.env` file. The local template and local Kokoro voice work without API keys. Gemini is needed for AI concept writing/revisions, Tavily for optional research, and ElevenLabs for the optional API voice. Keep `.env` private.

FFmpeg encoding is provided by `imageio-ffmpeg`. **Install FFmpeg with FFprobe on PATH** for upload validation and final quality checks. The app checks before generating media. Local Kokoro downloads its model files (about 120 MB) on the first explicit generation request; subsequent runs reuse the cache.

## Review flow

1. Select **New campaign**. Enter the product, optional brand/variant, audience, goal, creative style and call to action. Add approved facts one per line, or leave them empty for an appearance-led campaign. Upload a main product image; a second angle and up to five style references are optional. Choose your brand accent colour.
2. Choose **Local template** for a starting plan without API requests, or **New AI concepts** for Gemini planning. Optional research uses only the competitors you enter, through Tavily. No other campaign's facts or assets are copied into the new campaign.
3. Review all three concepts and scene plans. Request AI changes or use **Edit script** to edit text, selected facts, footnotes and visual sources.
4. Choose **Approve plan and save**. Versioned JSON and readable Markdown scripts are saved before generation becomes available.
5. Choose **Generate representative sample**. Review the video, narration, music and captions.
6. Choose **Approve sample** to unlock the other two videos. Product images are sufficient for all three videos. If you selected lifestyle footage, upload the planned licensed clips before rendering the story.
7. Review all three videos and choose **Approve final videos**. Download individual MP4s, captions or the final ZIP bundle.

Changing a script invalidates the active approval and returns to plan review. Refreshing a page, switching screens or restarting the server never supplies an approval or starts generation. Checkpoints and files persist. Background jobs continue while the browser remains usable; an interrupted processing step can be retried after an app restart.

## Clean workspace

The UI opens with your own campaigns or an empty workspace. The old notebook example is excluded from the campaign selector and previews, and notebook campaigns are not imported automatically. Existing files remain on disk independently of the UI. The application does **not** read or execute `.ipynb` source at runtime.

## Screens

- **Overview:** current action, campaign summary, opening hooks and sample preview.
- **Campaign brief:** create campaigns for any product, edit briefs, view approved facts and upload optional lifestyle footage. Saving brief changes resets approvals and creates a fresh local draft; previous media files remain on disk.
- **Plan & approvals:** concepts, timed scenes, AI revision feedback, manual script editing and approved-plan downloads.
- **Video review:** previews, narration-only playback, export checks, feedback and final downloads.
- **API connections:** explicit small requests with actual HTTP responses and hidden credentials.
- **Architecture:** the compiled LangGraph workflow, including all approval and revision routes.

## Modules

```text
streamlit_app.py          Entry point and navigation
app_pages/               Streamlit screens
assets/                  API attribution logo and credits
video_agent/
  config.py              Paths and production defaults
  campaigns.py           Product-independent briefs, image uploads and local template
  models.py              Validated scene, campaign and review schemas
  prompts.py             Writer, reviewer and revision instructions
  validation.py          Claims, narration, caption and timing checks
  planning.py            Gemini planning and Tavily research
  connections.py         Provider diagnostics and secret redaction
  service.py             Validated application commands
  workflow.py            Persistent LangGraph approval workflow
  storage.py             Atomic files and versioned approvals
  jobs.py                Background jobs, progress and errors
  documents.py           Readable campaign scripts
  rendering/
    voice.py             Shared Kokoro model and optional ElevenLabs adapter
    audio.py             Speech timeline, original music and audio mix
    visuals.py           Brand-aware product, lifestyle and reveal composition
    pipeline.py          Encoding, captions, quality checks and delivery
    renderer.py          Campaign-scoped approval-checked rendering
tests/                   Backend and Streamlit AppTest checks
```

## Files and credentials

Every new campaign stores **its own uploaded product and style images** with a SHA-256 manifest. Facts, product identity, CTA, accent colour and research are campaign-specific. Main/detail images preserve their original bytes and proportions. A single main image can fill both image roles. Optional style references guide the AI writer but never become finished ad frames.

The application stores its own data under **`data/studio/`**:

```text
data/studio/
  checkpoints.sqlite
  api_checks.json
  campaigns/<campaign-id>/
    project.json
    brief.json
    approved_plans/       Versioned JSON and readable scripts
    assets/               Campaign-owned product/style images and manifest
    input/                Optional uploaded lifestyle footage
    renders/              Cached audio, frames, logs and render metadata
    deliverables/vNNN/    MP4s, captions and final campaign ZIP
    last_job.json
```

Set `CFC_DATA_DIR` before starting the app to use another application data directory. The repository excludes local `.env` files, uploaded assets, saved campaigns, model weights, generated videos, caches and the earlier notebook experiments. A fresh clone starts with an empty workspace.

Keys remain in **`.env`**, environment variables or optional Streamlit secrets. Copy `.env.example` when setting up a new computer. Keys are never written into campaign state or API-test output.

## Free tools and API limits

The default voice is **local Kokoro**, with local NumPy music, Pillow visuals and FFmpeg encoding. No speech API key is needed. The Python 3.14-compatible Kokoro fork is pinned to a reviewed commit and archive checksum in `requirements-tts.txt`. Existing model files are reused; missing model files download on an explicit generation request.

This demo creates motion graphics from uploaded PNG, JPEG or WebP images and optional permitted footage. Layout headers use your product/brand, and feature text comes from the approved scene rather than fixed specifications. It does not fabricate photorealistic new product angles, packaging or actors. Output remains three 20-second vertical English videos.

Gemini and Tavily use account quotas. The app never switches to a paid model automatically. A failed AI planning step offers **Use saved draft instead** so you can continue with local editing and explicit approval.

ElevenLabs is optional and requires a key with TTS access, an available voice ID and credits. Its free outputs require **non-commercial use with attribution**; use an appropriately licensed account for commercial campaigns. The optional adapter is tested without spending live TTS credits. See [ElevenLabs billing and licensing](https://elevenlabs.io/docs/overview/administration/billing).

Coverr diagnostics check metadata only. API access does not clear footage for these product ads; see [Coverr API terms](https://api.coverr.co/docs). Upload your own or separately permitted lifestyle footage, or use the image-based workflow.

## Test

```cmd
.venv\Scripts\activate
python -m pip install --no-compile -r requirements-dev.txt
python -m pytest tests -q
```

Tests use isolated data directories, synthetic product images, simulated generation for approval flows and native Streamlit `AppTest`. They do not consume live API/TTS credits. Optional integration checks use locally cached voice models or the original notebook fixtures and are skipped when those local files are absent.
