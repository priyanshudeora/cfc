# Currency, setup and inference review — 2026-11-07

## Implemented behavior

- `video_agent/facts.py` preserves currency symbols, SI-prefix case, denominators, powers and temperature scales. INR/rupee and explicit spelling aliases are canonicalized; different currencies and units are not converted. Headlines, narration, captions and footnotes use the same conservative fact checks. Unsupported rewrites still require fact review.
- `Planner.gemini_key()` rejects missing/blank credentials before model-access HTTP requests or new model reservations. Valid saved parsed responses remain usable offline. A failed AI revision preserves its saved plan, and **Use saved draft instead** also supports sample/selected-video revision failures, returning to fresh plan approval. Manual editing remains available.
- Both dependency locks are generated with `--universal --generate-hashes`. Conditional packages include `pexpect`/`ptyprocess` on non-Windows systems and `appnope` on macOS. The upstream Kokoro environment remains separate from the Python 3.14 app.
- Speech subprocesses receive `ORT_DISABLE_TELEMETRY=1` before interpreter/native initialization. The worker sets it again before importing ONNX Runtime and retains `disable_telemetry_events()` before session creation. Inference does not download models.

## Installation evidence

All installs below used `--no-cache --require-hashes` and initially empty destinations. All dependency artifacts were retrieved publicly; existing application/voice installations were not accepted as evidence.

- Windows x64: Python 3.14.7 application, 95 packages, `tmp/review-app-fresh`; Python 3.12.14 speech, 16 packages, `tmp/review-tts-fresh`. Both succeeded on this machine.
- Linux x86_64: app, 96 packages including `pexpect==4.9.0` and `ptyprocess==0.7.0`, `tmp/review-linux-app`; speech, 16 packages, `tmp/review-linux-tts`. These are cross-target package installations on Windows, not native Linux execution.
- macOS ARM64: app, 96 packages, `tmp/review-macos-app`; speech, 16 packages, `tmp/review-macos14-tts`. The speech target requires macOS 14+. The initial macOS 13 speech attempt correctly failed because ONNX Runtime 1.31.1 has no compatible wheel for that target. These are cross-target package checks, not native macOS execution.

These are historical 7 November package-install measurements, before the dependency consolidation. For current installation use `uv sync --locked` and the separate TTS setup in README.md. Native Linux/macOS execution remains unverified.

## Cached speech with native outbound networking denied

The [pinned ONNX Runtime privacy documentation](https://github.com/microsoft/onnxruntime/blob/v1.31.1/docs/Privacy.md) explains that calling the API after native initialization can permit initial telemetry. It documents the earlier environment opt-out. This identifies a startup gap in the previous implementation; no deliberate baseline telemetry event was sent during this review.

`scripts/verify_voice_network_isolation.py` copies the actual worker, cached checksum-verified models and a freshly installed Python 3.12 speech runtime into a private directory. It launches the worker in a Windows AppContainer with **zero capabilities**, confirms the AppContainer process token and attempts a native Winsock connection to TEST-NET `192.0.2.1`. The connect returns `WSAEACCES (10013)`, while real Kokoro synthesis succeeds. [Windows documents AppContainer isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer).

Evidence: `tmp/voice-network-isolated-review/report.json`; actual audio is retained there. It reports 24 kHz audio, 2.731 seconds, nonzero RMS 0.07278, Kokoro 0.6.1, ONNX Runtime 1.31.1 and telemetry opt-out `1`. No model download occurs. The verifier includes a private-temp-directory ACL compatibility shim for Python 3.12/phonemizer; it does not replace inference or change production file ACLs. The temporary AppContainer profile is removed afterward. Ordinary production workers use the opt-out but are not automatically AppContainer-sandboxed.

Example verification command (Windows, uses disk space for private copies):

```text
python scripts/verify_voice_network_isolation.py --output tmp/voice-isolation-new --python tmp/review-tts-fresh/Scripts/python.exe
```

## Automated versus actual execution

The full suite passed: **159 tests**, 13 existing LangSmith deprecation warnings, 819.75 seconds on this memory-constrained machine. Tests include currency/unit changes across all four copy fields, blank-key zero-request/zero-reservation behavior, saved/manual revision recovery, early telemetry configuration, existing UI/approval/usage/cache/recovery/FFmpeg behavior. Controlled provider tests do not establish live provider inference.

Actual ComfyUI inference and revision evidence is saved separately in `tmp/scene-revision-review/report.json`. The verification uses the application service, LangGraph approvals, budgets, cache, actual ComfyUI submit/poll/download and FFmpeg export. Approvals are explicitly simulated **technical test approvals** in an isolated `tmp` campaign, not human advertising approval. Production campaigns are untouched. See the completed inference section below for final IDs and results.

## Actual new footage and revised export: passed technically

- New provider job: **`b636895c-9a18-4524-95dc-b5ce7071f5d3`**. A previously unused prompt/seed and generation key were checked before submission. Seed: `508647958`; new direction: slow camera drift left in neutral studio light. Application-observed completion time: **913.63 seconds**, including the cold-load/memory-pressure delay. Do not substitute the earlier faster benchmark timings for this run.
- Actual device: NVIDIA GeForce RTX 4050 Laptop GPU, CUDA with CPU offloading. Workflow: `workflows/ltx-2b-gguf-i2v.json`, SHA-256 `5aed880e0cbd28ba427ee347289d36b5855388aa3b50b9b717cc912cc278fa53`. Models: `ltx-video-2b-v0.9-Q3_K_S.gguf`, `t5-v1_1-xxl-encoder-Q3_K_S.gguf`, `LTX-Video-VAE-BF16.safetensors`. The evidence report retains the profile and model hashes.
- Genuine image-conditioned model inference: `LoadImage → LTXVImgToVideo → SamplerCustom → VAEDecode → SaveWEBM`. Native clip: **192×320, 49 frames at 24 fps, approximately 2.042 seconds**, 20 steps. The clip loops to fill its 3-second scene. The 20-second portrait export is an FFmpeg assembly, not native 20-second generation.
- Playable raw scene: `tmp/setup-demo-20261107/campaigns/generation-verification-only-b68dfc35/cache/generation/5cb903b80af3dc816ba0daef98435215/generated.webm`. SHA-256: `2126787ac3ea9e1d3c618789567e18804d75c539ff9ce3ddb563434374594eec`.
- Completed new export: `tmp/setup-demo-20261107/campaigns/generation-verification-only-b68dfc35/deliverables/v005/03_product_reveal.mp4`; approved technical bundle: adjacent `campaign.zip`. Videos 1 and 2 are cache hits with exactly the prior MP4 hashes. Four unaffected image-animation scene cache entries were verified. Source manifests explicitly identify one generated opening and four **image-animation** scenes, rather than claiming the whole video is AI footage.
- The verification helper initially compared raw-plan numeric types against canonical renderer cache keys. That assertion was corrected, and the already completed first export was recovered without another submission. Prior deliverables remain intact.
- Revision direction: slow camera drift right in warmer studio light, seed `508647959`. Saving the revision required a **fresh plan approval**; the old approval was rejected. The cached representative sample was reviewed/approved again in the technical workflow. Batch preflight then rejected insufficient available RAM **before any second new footage submission**.
- Initially, available RAM fell below the measured profile's **1.5 GiB** requirement (approximately 0.7 GiB at diagnosis, 0.127 GiB at the first audit). Clearing idle ComfyUI model caches through `/free` did not restore enough memory then. On continuation, available RAM had recovered to approximately **3.7 GiB** and the queue was empty. The application's existing **Retry** path resumed the approved batch. Hardware checks were not weakened, unrelated user processes were not stopped, and creation mode was not changed.
- Revised provider job: **`b4915f19-d29e-4e7d-a3a3-d459da8069cd`**; application-observed completion time **154.72 seconds**. Same measured workflow/device/settings and input image; changed prompt and seed. Real native clip: `tmp/setup-demo-20261107/campaigns/generation-verification-only-b68dfc35/cache/generation/957712ed5710061fdaa1532339b85355/generated.webm`. Decoded 64×64 RGB frame-stream SHA-256 differs: first `6111c050355694f684eb0dbc226be3d2b11e40724fe26dd370123c8fcbd818fa`, revised `f6ea0be623b968ab400bab7ac40e67b1fdd4237309579b9e1b1c2ecec6a78db0`. This proves changed pixels, not just changed metadata.
- Completed revised export: `tmp/setup-demo-20261107/campaigns/generation-verification-only-b68dfc35/deliverables/v006/03_product_reveal.mp4`; adjacent `campaign.zip` contains the final technical-test bundle. Videos 1 and 2 retained their exact prior MP4 hashes, and all four unaffected scene clips were reused. Six total generation tasks are complete: four historical jobs plus these two new jobs. All usage records are settled. The final audit reports approximately **4.532 GiB** available RAM.

**Remaining limitations:** native Linux/macOS setup and inference remain unverified because those hosts are unavailable. Both new inference/export rounds passed technically, including recovery, fresh approvals and cache reuse. Product appearance did **not** pass human review: inspection of the revised contact sheet shows severe identity drift from the supplied blue bottle. This low-resolution experimental model output must not be treated as approved advertising. No further model run was launched to conceal that limitation.

Recovery can be reproduced only for a matching unfinished technical verification report:

```text
python scripts/verify_scene_revision.py --simulate-approvals --data-dir tmp/setup-demo-20261107 --campaign generation-verification-only-b68dfc35 --output tmp/scene-revision-review --resume-revision
```

The verifier rejects a completed report; this command is documented for interrupted runs rather than for resubmitting the now-completed scene. Its recovery history retains the earlier RAM failure.

`tmp/current-review-audit.json` records dependency counts, lock hashes, actual task statuses, settled usage and final memory evidence. No paid provider was used. No GitHub push was performed.

## Changed files

- Fact and credential validation: `video_agent/facts.py`, `video_agent/planning.py`.
- Saved-plan revision recovery: `video_agent/service.py`, `app_pages/plan.py`.
- Early native telemetry opt-out: `video_agent/rendering/voice.py`, `scripts/tts_worker.py`.
- Portable hash locks: `requirements-app.lock`, `requirements-tts.in`, `requirements-tts.txt`.
- Regression coverage: `tests/test_meaning_and_missing_key.py`, `tests/test_voice_startup_privacy.py`.
- Reproducible actual-execution verifiers: `scripts/verify_scene_revision.py`, `scripts/verify_voice_network_isolation.py`.
- Setup/evidence documentation: `README.md`, this file.

Ruff `F/RUF059`, formatting checks and `git diff --check` pass. The improved frontend styling, generation adapter, approval enforcement, budget implementation and separate Kokoro runtime are preserved. No desktop/mobile browser inspection was repeated for this limited UI recovery-button change; the existing Streamlit automated checks passed. The actual new export's contact sheet was inspected: the generated opening visibly changes the illustrated bottle's appearance, while the four image-animation scenes retain the supplied illustration. This is a technical inference result, not product-fidelity approval.
