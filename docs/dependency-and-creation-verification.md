# Dependency and footage-source verification — 9 November 2026

## Starting state and scope

Branch `main`; working tree clean before this task. Existing generation, scoped approval receipts, budgets, persistent caches, recovery, frontend styling and separate Kokoro runtime were inspected first. Nothing was pushed or committed.

The existing two user campaigns were saved with the legacy editing default: **15 supplied-media scenes each, zero ComfyUI tasks**. Their older outputs do not contain detailed source manifests. The saved plans and absence of submissions establish why these campaigns use image animation. No user campaign was converted or regenerated automatically. Trace: `tmp/nov09-creation-trace.json`.

## Three dependency files

Only `pyproject.toml`, `uv.lock` and `requirements-tts.txt` remain as root dependency manifests/locks. The seven superseded files were removed. All existing locked package versions present in the new lock were retained; new packages supply the optional JupyterLab server. Dependency groups are optional: `test` (pytest/Ruff), `playwright` (browser checks plus tests), `notebook` (JupyterLab, ipykernel, widgets). No optional group is installed by default.

Fresh installations used **cache-disabled public downloads** into initially empty environments:

- App only: Python **3.14.7**, `uv sync --locked --no-cache`, **72 packages**, `tmp/nov09-app-only`. Testing, Playwright, Jupyter and Kokoro are absent from this environment.
- All optional groups: Python **3.14.7**, `uv sync --locked --no-cache --group test --group playwright --group notebook`, **147 packages**, `tmp/nov09-fresh-app`.
- Separate voice: Python **3.12.14**, `scripts/setup_tts.py --environment tmp/nov09-fresh-tts`, **16 packages**, `--require-hashes --no-cache`. The new environment performed real cached-model inference in both demos; no speech models were downloaded during inference. `uv venv --no-project` keeps the voice runtime independent of the app's Python requirement.

For a custom app verification directory set `UV_PROJECT_ENVIRONMENT` for the setup command; ordinary Windows checkout commands are in README.md. The launcher ran successfully using the fresh app-only environment. It invokes `uv sync --locked`, then starts Streamlit without another sync. App: **http://127.0.0.1:8520** (isolated AI-sample data). Package/version audit: `tmp/nov09-dependency-audit.json`. `uv lock --check` passes.

## Source selection, stage rules and failure behavior

- **Planning / AI concepts** controls script planning only. **Creation** controls executable scene media. `apply_creation` compiles Generate into model-backed scenes throughout all three videos. Validation rejects any supplied-image scene in Generate mode. Combine generates the scenes marked for AI and exposes supplied assets in other scenes. Its mixture is evaluated across the campaign, so remaining videos can explicitly use supplied assets.
- `CampaignService.generation_approval_blocker` and renderer preflight share `stage_generation_issue`. At plan approval and sample generation, they count only video 1. A sample-only profile allows one generated sample scene, rejects two or more, and blocks **any** generated scene in the remaining videos at sample approval—even if there is just one. The UI consumes the backend gate rather than using the campaign-wide estimate.
- Missing profile and zero/exhausted/insufficient allowances give explicit `COMFY_PROFILE` / `CFC_DEFAULT_LIMITS_JSON` setup and fresh-review steps. Cache hits and already accepted jobs do not require a new generation allowance. Existing saved limits are never replaced silently.
- Preflight and generation errors name the required scenes. ComfyUI execution errors include the provider's exception message and retain the job ID and provider error record. Finished clips survive assembly errors and remain available for Retry. No automatic switch to image animation exists; changing Creation/scene mode requires an explicit user edit and fresh approval.
- Video review shows **Generated clips**: raw video, workflow, models, workflow hash and original job ID. Cached clips retain their original ID. Partially completed clips also appear when a later scene/assembly fails. Plan cards identify every scene as AI model footage or supplied media/image animation.

## Automated checks

Final full suite: **172 passed**, 13 existing LangSmith deprecation warnings, **164.70 seconds**, run with the clean all-groups environment. Added coverage includes one-AI-scene sample approval/generation, multi-scene blocking, one-scene batch blocking, UI/backend agreement, zero/spent allowances, Generate mode enforcement, raw-clip provenance, retained original job IDs, exact model errors, and transient/permanent Windows atomic-write failures.

Ruff `F/RUF059`, formatting and `git diff --check` pass. Controlled provider responses in tests are not inference evidence. The live runs below are separate.

## Actual FFmpeg + fresh Kokoro demo

Evidence: `tmp/nov09-local-demo/demo_report.json`; campaign `local-workflow-verification-28180c3a`.

All three 20-second, 1080×1920 H.264/AAC videos exported with actual Kokoro speech from the new Python 3.12 environment, original instrumental music, burned captions and separate SRT files. This demo is **image animation**, with **zero generated footage** and Python HTTP requests blocked. Audio duration/finite samples/energy, technical exports and media contracts passed.

Video 3 was revised through the application timeline. The old generation approval was rejected; fresh plan approval and sample review were exercised. Videos 1 and 2 were cache hits and retained identical MP4 hashes, while video 3 changed. Speech was reused. Final bundle:

`tmp/nov09-local-demo/campaigns/local-workflow-verification-28180c3a/deliverables/v002/campaign.zip`

The initial export completed but Windows returned **WinError 5** while atomically replacing `last_job.json` under OneDrive. A bounded retry was added only for known Windows lock/access codes (5/32/33); permanent failures still raise, preserving the previous JSON. The completed campaign was resumed with `--resume-exported`; completed initial videos were not regenerated. The report retains the original error. Three regressions verify transient recovery, bounded permanent errors and old-file preservation.

## Actual RTX 4050 sample: playable model output used in export

Evidence: `tmp/nov09-ai-sample/ai_sample_report.json`; campaign `ai-sample-verification-a17965d7`.

- **New job:** `e370b7be-e1b4-4ceb-bf7d-28e853375764`, one submission/attempt, complete; application-observed generation time **144.33 seconds**. ComfyUI logged **141.80 seconds** execution. No paid provider or model-download requests were permitted.
- **Device:** NVIDIA RTX 4050 Laptop GPU, CUDA with CPU offloading; measured profile and model/workflow hashes preserved. No hardware check was weakened.
- **Workflow:** `ltx-2b-gguf-i2v.json`; actual product image uploaded and connected through `LoadImage → LTXVImgToVideo → SamplerCustom → VAEDecode → SaveWEBM`.
- **Models:** `ltx-video-2b-v0.9-Q3_K_S.gguf`, `t5-v1_1-xxl-encoder-Q3_K_S.gguf`, `LTX-Video-VAE-BF16.safetensors`.
- **Native footage:** 192×320, 49 frames, 24 fps, 20 steps, approximately **2.042 seconds**. Explicitly looped to fill its 3-second scene. The final export is 20 seconds assembled by FFmpeg; it is not native 20-second generation.
- **Sample composition:** **1 AI-generated scene + 4 supplied-image animation scenes**. Fresh plan approval preceded footage submission. All usage reservations settled. Human advertising/appearance approval remains outstanding.
- **Raw clip:** `tmp/nov09-ai-sample/campaigns/ai-sample-verification-a17965d7/cache/generation/2ba7656ff68f1e1bc05d976984cc1e08/generated.webm`.
- **Export:** `tmp/nov09-ai-sample/campaigns/ai-sample-verification-a17965d7/deliverables/v002/01_product_demo_sample.mp4`.
- **Export-use verification:** manifest source/job ID matches the actual output; raw-to-FFmpeg-selection mean absolute RGB error **1.696 / 255**; assembled-scene-to-final-export media-region error **0.538 / 255** at one second. The source is decoded/validated, and pixel checks confirm model footage entered the actual export, beyond checking a recorded path.
- **Actual cache reuse:** repeated retrieval with all HTTP rejected kept the same original job ID, changed no tasks/usage and submitted zero new jobs. Evidence: `tmp/nov09-ai-cache-check.json`.

The verifier's initial empty-directory creation conflict occurred before campaign/job creation; it was corrected and only the provably empty setup directory was resumed. No footage was resubmitted. Known task IDs stay recoverable through the normal application if a live run is interrupted.

## Browser playback and visual inspection

Headless Microsoft Edge tested the **actual app and actual media**, at 1440×1000 and 390×844. Both the 20-second final MP4 and 2.042-second raw WebM loaded dimensions and duration, advanced during playback and reported no media errors. The actual job ID/model/workflow appear in Video Review. No JavaScript errors or horizontal overflow were detected. Evidence and screenshots: `tmp/nov09-ui-screenshots/report.json`, `desktop.png`, `mobile.png`, `raw-clip-desktop.png`, `raw-clip-mobile.png`.

Visual inspection confirms real generated content but also hallucinated product lettering/background changes. The heavily quantized low-resolution model does not reliably preserve product identity. This is a technical generation/export demonstration, not approved production advertising. A full 15-AI-scene campaign and native Linux/macOS execution were not run; the requested single live clip and sample export passed on this Windows RTX 4050 machine. External editor MCP, Jev, automatic semantic visual review and word alignment remain unchanged future work.

## Changed files

- Dependency consolidation: `pyproject.toml`, `uv.lock`, `requirements-tts.txt`; seven obsolete dependency files removed.
- Launcher/setup: `run_app.cmd`, `scripts/setup_tts.py`.
- Stage policy and source/failure handling: `video_agent/generation_contract.py`, `video_agent/preflight.py`, `video_agent/service.py`, `video_agent/generation.py`, `video_agent/rendering/renderer.py`.
- Frontend: `app_pages/brief.py`, `app_pages/plan.py`, `app_pages/videos.py`, `video_agent/ui.py`.
- Windows write recovery: `video_agent/storage.py`.
- Regressions: `tests/test_creation_stage_gates.py`, `tests/test_atomic_windows_recovery.py`, `tests/test_generation.py`.
- Reproducible demos: `scripts/verify_ai_sample.py`, `scripts/verify_local_demo.py`.
- Documentation: README.md and the setup/frontend/historical/current verification documents.

Reproduce locally with `uv sync --locked --group test`, `uv run --locked --no-sync python -m pytest tests -q`, and the README setup commands. `scripts/verify_local_demo.py --simulate-approvals --output <fresh-folder>` checks the three-video local workflow; `scripts/verify_ai_sample.py --simulate-approvals --main-image <product.png> --output <fresh-folder>` runs one real sample using the configured measured profile. These CLI approvals are explicitly technical test receipts, not human product-fidelity approval.
