# Local AI footage: implementation and measured limits

Verified on 5 November 2026. The CFC application remains on Python 3.14.7 and Streamlit. ComfyUI runs in a separate Python 3.12.14 environment; Torch and its CUDA dependencies were not installed into the application environment.

## What executes

Planning remains **Local draft / AI concepts**. The independent creation choice is saved in the brief:

- **Edit existing images and videos:** existing supplied-asset workflow, with no ComfyUI requirement.
- **Generate new AI footage:** generated image-conditioned footage in every scene.
- **Combine generated footage with supplied assets:** the local starting plan generates the opening scene of each video and edits supplied images/clips for the remaining scenes. Advanced plans can combine these sources differently.

Each generated scene has an executable mode, conditioning asset, prompt, seed, measured output settings, requested scene duration and purpose. Changing descriptive direction alone does not execute a generation change. Reference-only assets cannot condition the product model. Hybrid scenes are labelled **compositing**, including the static overlay; they are not presented as evidence of image conditioning.

The execution order is brief -> plan/estimate -> plan approval -> prepare sample footage -> voice/music/FFmpeg -> sample approval -> remaining footage/assembly -> final review/export. Inference runs in the separate ComfyUI process, polled by existing background workers. No Streamlit request thread runs Torch inference. FFmpeg remains the editor.

## Actual measurements

Hardware: NVIDIA GeForce RTX 4050 Laptop GPU, 6,141 MiB VRAM; Intel Core 5 210H, 8 cores / 12 logical processors; approximately 16 GB physical RAM. Other applications were open. These are observed runs, not throughput guarantees.

- CUDA, 192 x 320, 9 frames, 24 fps, 20 steps: **49.89 seconds**, sampled peak whole-device GPU use **2,955 MiB**, sampled peak process RSS **4.37 GiB**.
- CUDA, 192 x 320, 49 frames, 24 fps, 20 steps: **53.90 seconds**, sampled GPU use **2,931 MiB**, process RSS **4.56 GiB**.
- Separate CUDA conditioning comparison, same prompt/seed/settings, different supplied image: **152.37 seconds**. The first decoded frame changed; mean RGB difference at 64 x 64 was **80.31**. This verifies that the image affects inference, not product fidelity.
- **CPU-only**, `--cpu`, 192 x 320, 9 frames, 24 fps, 20 steps: **1,198.18 seconds**, sampled GPU use **0 MiB**, process RSS **3.98 GiB**. Logs place the text encoder, VAE and diffusion model on CPU. CPU batch is disabled. GPU execution with weight offloading is separately labelled CUDA.

Memory is sampled while polling; GPU figures cover the whole device and RSS covers the inference process. They are not exact tensor-allocation peaks. Memory pressure was observed: the lowest available physical RAM was approximately 117 MiB in the first GPU run and 425 MiB in the CPU run. CPU experiments require a RAM check and should not be treated as a practical campaign batch engine.

The enabled GPU settings are restricted to the measured 192 x 320 / 9 or 49 frames / 24 fps / 20 steps / strength 1 combinations, with one inference job at a time. CPU enables only its measured 9-frame combination, with one generated scene per approval stage and no batch. Readiness checks actual server type/CPU flag, software and execution flags, model checksums, workflow connections, physical/available RAM and the measured GPU's memory. Lowering the old `COMFY_MIN_VRAM_GB` setting does not establish compatibility; that setting is no longer used.

**Quality limitation:** the small, heavily quantized, older model visibly changes the illustrated bottle's appearance. It is an experimental generation demonstration, not approved product-faithful advertising. Higher resolution, other models, native 20-second generation and a complete 15-generated-scene campaign were not live-verified. A 49-frame clip is about 2.04 seconds; FFmpeg explicitly loops it to the approved scene duration. The final video is 20 seconds, not a native 20-second model output. Human review remains mandatory.

## Live integration evidence

Local-only evidence is retained under `tmp/` and is excluded from Git:

- `tmp/comfy-verification/{gpu-short,gpu-49,gpu-conditioning-b,cpu-short}/report.json`: submitted graph, input/output hashes, provider ID, device, settings, elapsed time, memory samples, provider history and full decoded clip metadata.
- `tmp/comfy-verification/conditioning-comparison.json`: controlled real image-conditioning comparison.
- `tmp/generated-demo-20261105/generation_demo_report.json`: actual CFC three-video flow and targeted revision. Four generation tasks completed, with no stranded reservations. Videos 1 and 2 retained identical MP4 hashes and reported cache hits when video 3's generation seed changed.
- `tmp/generated-demo-20261105/audio-verification.json`: non-silent voice/music tracks, captions containing the CTA, successful full decode, and a local whisper.cpp transcription of the actual mixed sample audio matching the script.
- Final files are in `tmp/generated-demo-20261105/campaigns/generation-verification-only-520c4b00/deliverables/v002/`. The ZIP contains three MP4s, SRTs, approved-plan data and per-video scene-source manifests.

The live campaign used **combined** creation: one genuinely generated scene plus four supplied-image scenes per video. Approval receipts were explicitly simulated in this isolated technical-test campaign. Product appearance was **not** approved for advertising. Actual planning/voice inference used no paid provider. The verification script blocks non-local Python HTTP requests.

## Reproduce in CMD

The selected older LTX-Video 2B GGUF model, quantized T5 encoder and separate VAE come from the conversion author's repositories: [LTX weights and VAE](https://huggingface.co/city96/LTX-Video-gguf), [T5 weights](https://huggingface.co/city96/t5-v1_1-xxl-encoder-gguf). The underlying model is [Lightricks LTX-Video](https://github.com/Lightricks/LTX-Video). The [GGUF loader](https://github.com/city96/ComfyUI-GGUF) and [ComfyUI HTTP routes](https://docs.comfy.org/development/comfyui-server/comms_routes) are used directly. Model licenses still apply.

`setup_comfy.py` pins ComfyUI and GGUF source revisions, creates a separate environment, and downloads checksum-verified weights only with `--download-models`. It can omit the optional template gallery's large example-media bundles; `--include-gallery` installs those too. The current machine was provisioned with the full gallery. The helper's fresh minimal-gallery and CPU-wheel-only installations were not separately rerun from an empty machine.

From the project directory:

```cmd
.venv\Scripts\activate
python scripts\setup_comfy.py --root C:\CFC-Comfy --device cuda --download-models
```

Start ComfyUI in a separate CMD window:

```cmd
cd /d C:\CFC-Comfy\ComfyUI
..\env-cuda\Scripts\python.exe main.py --offline --disable-all-custom-nodes --whitelist-custom-nodes ComfyUI-GGUF --disable-dynamic-vram --reserve-vram 0.8 --use-pytorch-cross-attention --fp32-vae --cache-none --preview-method none
```

Back in the activated application environment, identify the actual inference Python PID, not its small virtual-environment launcher. Task Manager or `psutil.process_iter(['name','cmdline'])` can identify the `main.py` process. Replace `INFERENCE_PID` and the image path below:

```cmd
python scripts\verify_comfy_workflow.py --workflow workflows\ltx-2b-gguf-i2v.json --image C:\path\product.png --device cuda --pid INFERENCE_PID --frames 9 --output tmp\my-gpu-short
python scripts\verify_comfy_workflow.py --workflow workflows\ltx-2b-gguf-i2v.json --image C:\path\product.png --device cuda --pid INFERENCE_PID --frames 49 --output tmp\my-gpu-long
python scripts\register_comfy_profile.py --report tmp\my-gpu-short\report.json --report tmp\my-gpu-long\report.json --workflow workflows\ltx-2b-gguf-i2v.json --model-root C:\CFC-Comfy\ComfyUI\models --output tmp\my-gpu-profile.json --allow-batch
```

If polling times out, `--resume` with the original workflow/image/output polls the saved provider ID. It does not submit again. An ambiguous submission without an ID requires queue/history reconciliation, never a blind retry.

Set `.env` to the registered evidence and explicit device:

```dotenv
COMFY_URL=http://127.0.0.1:8188
COMFY_DEVICE=cuda
COMFY_WORKFLOW=workflows/ltx-2b-gguf-i2v.json
COMFY_PROFILE=tmp/my-gpu-profile.json
COMFY_POLL_TIMEOUT_SECONDS=1800
CFC_DEFAULT_LIMITS_JSON={"generation_jobs":15}
```

That finite server allowance applies only to new campaigns. Existing campaigns retain limits and rates. An explicit **Review updated generation allowance** action can adopt a higher server job allowance; it revokes previous grants and requires approval of the new plan/estimate. Cost caps, reservation accounting and other limits stay active. A real accepted failed job consumes a generation allowance; an explicit graph rejection consumes zero. Successful matching clips are reused even after a headline/layout change.

On this machine, the configured GPU profile is `tmp/comfy-profiles/gpu.json` and the optional CPU profile is `tmp/comfy-profiles/cpu-experimental.json`. GPU ComfyUI is on port 8188; the separate CPU test server is on 8189. To experiment with CPU, start the separate environment with `--cpu` and the recorded common flags, benchmark it, register without `--allow-batch`, and select `COMFY_DEVICE=cpu` plus that CPU URL/profile. CPU execution was verified using `--cpu` in the CUDA-capable separate environment; no model inference used the GPU. A separate CPU-wheel-only fresh install remains unverified.

Launch the normal application with `python -m streamlit run streamlit_app.py`. Create a new brief, choose creation independently of planning, review the estimate and follow all approval checkpoints. Editing-only campaigns do not require ComfyUI. Changing device/workflow/settings requires a fresh approved scope. Switching to editing is an explicit brief change.

To rerun the isolated actual-media test:

```cmd
python scripts\verify_generation_demo.py --simulate-approvals --main-image C:\path\product.png --output tmp\my-generation-demo
```

This test uses four generation jobs (three initial clips, one revised clip), cached local Kokoro models and real FFmpeg. It never resumes an existing campaign. Cancellation stops queued/future local work and removes owned pending ComfyUI jobs where possible; removal is checked against the queue and history. Accepted submissions conservatively consume one job even when removed before execution, and their reservations are settled. Running provider inference may finish. A provider restart that loses history may need explicit reconciliation. Failed downloads or media validation retry retrieval against the same known job; only an explicit scene revision requests different generation.

## Verification boundaries

Automated tests exercise policy, authorization, profile/device validation, real image binding, cache dependencies, known/unknown failures, recovery without another submission, cancellation, quotas and the Streamlit UI. Controlled HTTP responses in those tests do not establish model inference; the live evidence above does. Standalone CPU/GPU probes and the CFC generated-footage export were real executions.

Final automated run: `python -m pytest tests -q` — **98 passed in 120.40 seconds**, including 15 focused generation tests, with 13 existing third-party LangSmith deprecation warnings. This includes existing UI, authorization, claims, usage, approvals, revisions, exports and FFmpeg checks. The damaged-download recovery regression confirms one upload/submission, two retrieval attempts, one settled job and no duplicate generation.

Actual Streamlit browser inspection used isolated campaign data and existing completed media on port 8511. Eight screens at 1440 x 1000 and 390 x 844 covered independent creation selection, saved plans, actual generated video playback/export and scene-source provenance. No JavaScript errors or horizontal main-content overflow were observed. Screenshots and bounds checks are in `tmp/generation-ui-screenshots/`. The creation form was inspected without submitting it; no generation, approval or paid-provider actions were triggered during browser inspection. These are headless Edge viewport checks, not physical-phone tests. Screenshots were visually inspected separately from AppTest assertions.

External editing-app MCP, Jev, automatic semantic visual review and word-level caption alignment remain future work. Real user-recording transcription was not supplied; the new transcription check used actual generated MP4 audio. No repository was pushed.
