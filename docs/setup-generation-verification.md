# Reproducible setup and generation preflight

Updated 7 November 2026. This supplements the earlier measurements in [local-generation.md](local-generation.md). Existing campaigns, saved limits/rates, approvals and generation cache identities are preserved.

## Supported environments and public dependencies

The previously pinned `dayour/kokoro-onnx` archive returned HTTP 404 in a new direct request. It has been removed. Upstream [kokoro-onnx 0.6.1](https://pypi.org/project/kokoro-onnx/0.6.1/) supports Python below 3.14, so speech now runs in a separate **Python 3.12** process. The Streamlit application remains on **Python 3.14**. ComfyUI keeps its own separate environment.

- `requirements-app.lock`: public application packages, exact versions and SHA-256 hashes. The fresh install resolved 95 packages, including Streamlit 1.67.0. This does not upgrade an existing application environment automatically.
- `requirements-tts.txt`: all 16 upstream speech dependencies pinned with hashes, including Kokoro 0.6.1, ONNX Runtime 1.31.1, NumPy 2.4.6, phonemizer 3.6.0 and bundled espeakng-loader 0.2.4. No fork archive is required.
- `requirements-tts.in`: reviewed speech input pins; comments describe lock regeneration.
- `scripts/setup_tts.py`: creates an owned `.venv-kokoro`, installs with `--require-hashes --no-cache`, checks dependencies and downloads models only with `--download-models`. It refuses unrelated existing environments. A custom environment needs an explicit `CFC_TTS_PYTHON` path.
- `scripts/tts_worker.py`: persistent local CPU inference through a JSON-lines protocol. ONNX Runtime telemetry is disabled before inference-session construction. Startup performs a real short synthesis, so missing phonemizer/native-library dependencies fail before footage. Inference does not download models.

Both model files are size- and SHA-256-verified, including cached files. The checksums in `video_agent/voice_setup.py` matched a new download of the public upstream release assets. The fresh model folder is `tmp/fresh-voice-models-20261107`; a real speech WAV was produced there.

### Fresh installation in Windows CMD

Use Python 3.14.7 for the application. Start with a new directory for each environment; do not sync into an unrelated existing environment.

```cmd
python -m pip install uv==0.12.19
uv venv --python 3.14 .venv
uv pip sync --no-cache --require-hashes --python .venv\Scripts\python.exe requirements-app.lock
.venv\Scripts\activate
python scripts\setup_tts.py --download-models
copy .env.example .env
ffprobe -version
python -m streamlit run streamlit_app.py
```

The installer uses `uv` to obtain/select Python 3.12 separately. If using a custom speech directory:

```cmd
python scripts\setup_tts.py --environment C:\CFC-Voice --download-models
```

Then set `CFC_TTS_PYTHON=C:\CFC-Voice\Scripts\python.exe` in `.env`. Do not install the speech lock into the Python 3.14 app. Editing requires executable FFmpeg/FFprobe; Windows FFprobe installation remains an explicit external prerequisite.

The actual new installs used `tmp/fresh-app-20261107` (Python 3.14.7) and `.venv-kokoro` (Python 3.12.14), with package caches disabled. Both dependency checks passed. No pre-existing virtual environment was used as fresh-install evidence. An initial setup attempt mistakenly targeted an existing `.venv-tts`; its removed packages were restored and its 88-package dependency check passed. The installer was then changed to require ownership before modifying an existing directory, with regression coverage.

The pinned ComfyUI and GGUF source commits and all three pinned generation-weight URLs were also publicly accessible on 7 November (HTTP 200); results are in `tmp/comfy-public-download-check-20261107.json`. This accessibility check is separate from the real inference evidence and does not claim a new empty ComfyUI installation.

## Preflight and setup interface

Settings & tools → API connections shows independent **Editing**, **Local voice** and **AI footage** states. Files being present are labelled as needing verification, not successful inference. **Check local setup** checks actual tools, model hashes, speech initialization/warm-up and the actual ComfyUI profile/device without generating footage. Results are dated snapshots; recheck after changing the environment.

**Set up local voice / download models** is an explicit background job, displaying package/download progress and actionable errors. It never approves campaigns, submits footage or removes limits. Editing-only campaigns do not contact ComfyUI; narrated editing still requires the selected voice engine. The brief warns about a non-batch CPU profile before campaign submission: it can test one generated opening sample scene, while five-scene generated samples and remaining-video batches are blocked. Switching modes is always explicit.

Before footage preparation the renderer checks the approved stage grant, FFmpeg/FFprobe execution, actual product-file hashes and image decoding, then initializes the selected voice. Local initialization loads the real model and synthesizes a short warm-up; ElevenLabs requires configured credentials and a voice ID but account quota/remote voice access cannot be guaranteed without a provider request. No paid speech request is made by preflight. Generated scenes then require the saved verified scope, permitted stage, provider readiness and available allowance. The existing atomic per-job reservation remains authoritative immediately before submission.

Existing valid clips and known provider IDs are considered in preflight, so they do not require a second generation allowance. Completed generated clips remain intact after later speech/editing failure. Recoverable downloads/polls use the original provider ID, and ambiguous submissions still require reconciliation. Editing-only work has no ComfyUI readiness requirement.

## Estimates

Before plan approval the interface reports generated versus reused scene counts, sample and remaining job counts, native generated resolution, frame count, frame rate and duration, plus whether the clip loops to each requested scene duration. It explicitly distinguishes the final **20-second edited export** from the tested **49-frame / 24 fps ≈ 2.04-second native clip**. Experimental shape/colour/branding drift and human-review requirements remain visible.

## Verification evidence

Automated ordering/policy tests use controlled failures; they are not inference evidence. Actual model generation and the fresh-install export run are recorded separately under `tmp/setup-demo-20261107`. No paid provider or GitHub push was part of this verification.

- **109 tests passed in 114.46 seconds**, with 13 existing third-party LangSmith deprecation warnings. This includes the existing UI, authorization, facts, usage, recovery and FFmpeg tests, plus missing-voice ordering, whole-batch allowance checks, completed-clip reuse after assembly failure, CPU guidance and unverified readiness labels.
- Ruff **F / RUF059** checks pass across the repository: wildcard imports, undefined/duplicate/unused imports and unused variables have been cleaned up. Changed Python files pass formatting; `git diff --check` passes. Broader inherited stylistic rules are outside this cleanup.
- **Actual missing-dependency check:** a new empty Python 3.12 environment was selected as the speech runtime. The actual renderer reported `ModuleNotFoundError: No module named 'numpy'` with the setup command. Footage POST attempts, generation task records and generation reservations were all zero. Evidence: `tmp/missing-speech-check-20261107/missing-speech-report.json`.
- **Actual generation:** four real image-conditioned ComfyUI jobs ran on the RTX 4050 Laptop GPU (`cuda:0`, 6,141 MiB), one at a time. Settings: 192 x 320, 49 frames, 24 fps, 20 steps, strength 1; native duration approximately 2.042 seconds, looped to a 3-second opening scene. Recorded elapsed times include upload/poll/download/validation, not just GPU kernels:
  - `d47684fa-6dcb-4b9d-8c44-302d25cead02`: 112.57 s.
  - `ff21b4d6-cb3e-4dbe-b55c-dae36dcc2323`: 86.43 s.
  - `a8f7a703-bb58-40d6-bc58-ad57c800f29a`: 86.32 s.
  - `a2b6b0b3-0732-4dae-9416-add778ef4bcf`: 86.47 s, the video-3 revision.
- **Actual export:** `generation_demo_report.json` records plan approval, sample generation, sample approval, remaining-video generation, final export and a targeted video-3 seed revision. Videos 1/2 retained identical hashes and cache hits. These were explicitly simulated technical-test approvals in an isolated campaign; product appearance was not approved for advertising.
- **Current export metadata and cache:** after correcting the old hardcoded in-process speech metadata, the same isolated plan received new technical-test approvals and exported version 3 using all completed clips/audio/video caches. Every video retained its previous MP4 SHA-256; **zero HTTP requests and zero new generation jobs** were permitted. `cached-export-report.json` records actual Python 3.12.14, Kokoro 0.6.1 and ONNX Runtime 1.31.1 for the separate voice worker. Old version-2 report metadata is historical; the follow-up report corrects that field without inventing new inference.
- **Output audit:** `verification-audit.json` records each provider ID, conditioning-image hash, clip path/settings/device/timing, three source manifests and the final ZIP. All three final MP4s fully decode at 1080 x 1920 / 30 fps / 600 frames / 20 seconds with audio, non-silent voice/music and readable caption artifacts. Each combines one actual generated scene with four image-animation scenes. The ZIP's CRCs pass, all four generation tasks are complete and there are no stranded usage reservations.
- **Actual frontend:** the fresh Python 3.14.7 / Streamlit 1.67.0 app runs at `http://127.0.0.1:8512` with isolated demo data. The normal app remains at port 8501. Six headless Edge screenshots at 1440 x 1000 and 390 x 844 cover actual readiness checks, the clarified estimate and exported videos; no JavaScript errors or horizontal main-content overflow were observed. Screenshots were visually inspected separately from AppTest. Files: `tmp/setup-ui-screenshots/`. These are viewport checks, not physical-phone tests.

Final outputs:

```text
tmp/setup-demo-20261107/campaigns/generation-verification-only-b68dfc35/deliverables/v003/
  01_product_demo_sample.mp4
  02_lifestyle_story.mp4
  03_product_reveal.mp4
  campaign.zip
```

The model's low resolution and visible product-shape drift remain experimental limitations. CPU-only inference was measured in the previous phase; CPU batch remains disabled and was not rerun here. ComfyUI's installed stack/profile was reused and exercised by real generation, rather than reinstalled as part of the speech repair. Other operating systems, fresh CPU-only Comfy wheels, paid ElevenLabs access/quota and physical phones remain unverified. External editor MCP, Jev, automatic semantic product review and word-level caption alignment remain pending. FFmpeg/FFprobe and the `uv` bootstrap are explicit system prerequisites, not silently downloaded during rendering.
