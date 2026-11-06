"""Verified local image-to-video generation, with durable submission recovery.

ComfyUI runs in a separate process/environment. No inference library is imported here.
A saved provider ID is polled again, never treated as permission to submit twice.
"""

from pathlib import Path
from urllib.parse import urlparse
from functools import lru_cache
from fractions import Fraction
import json
import subprocess
import time
import requests
from .storage import read_json, save_json, digest
from .cache import file_hash
from .timeline import FootageSettings
from .usage import charge
from .rendering.editor import run_ffmpeg


@lru_cache(maxsize=16)
def _model_hash(path, size, modified):
    return file_hash(path)


def settings_identity(value):
    return {
        k: value[k] for k in ["width", "height", "frames", "fps", "steps", "strength"]
    }


class ComfyFootage:
    LOCAL_NODES = {
        "UnetLoaderGGUF",
        "CLIPLoaderGGUF",
        "VAELoader",
        "CLIPTextEncode",
        "LTXVConditioning",
        "LoadImage",
        "LTXVImgToVideo",
        "LTXVScheduler",
        "KSamplerSelect",
        "SamplerCustom",
        "VAEDecode",
        "SaveWEBM",
    }

    def __init__(
        self, keys, control, cid, gpu_workers=1, *, assets=None, brief=None, note=None
    ):
        self.keys, self.control, self.cid = keys, control, cid
        self.assets, self.brief, self.note = (
            assets or {},
            brief or {},
            note or (lambda text: None),
        )
        self.url = (keys.get("COMFY_URL") or "http://127.0.0.1:8188").rstrip("/")
        parsed = urlparse(self.url)
        if parsed.scheme not in ["http", "https"] or parsed.hostname not in [
            "localhost",
            "127.0.0.1",
            "::1",
        ]:
            raise ValueError(
                "Only a local ComfyUI server is supported; no paid endpoint or fallback."
            )
        self.device = keys.get("COMFY_DEVICE") or "cuda"
        if self.device not in ["cpu", "cuda"]:
            raise ValueError("COMFY_DEVICE must explicitly be cpu or cuda.")
        self.timeout = max(
            30, min(7200, int(keys.get("COMFY_POLL_TIMEOUT_SECONDS") or 1800))
        )

    def get(self, route, **params):
        response = requests.get(self.url + route, params=params, timeout=(3, 30))
        response.raise_for_status()
        return response.json()

    def profile(self):
        path = Path(self.keys.get("COMFY_PROFILE") or "__unconfigured__")
        if not path.is_file():
            raise ValueError(
                "Configure COMFY_PROFILE with a real device benchmark. Installation alone is not generation verification."
            )
        profile = read_json(path)
        if profile["device"] != self.device or profile.get("max_concurrency") != 1:
            raise ValueError(
                "The benchmark must match COMFY_DEVICE and use one inference job at a time."
            )
        workflow = Path(self.keys.get("COMFY_WORKFLOW") or "__unconfigured__")
        if not workflow.is_file() or file_hash(workflow) != profile["workflow_sha256"]:
            raise ValueError(
                "The workflow differs from the measured profile. Verify it again before approval."
            )
        tested = []
        for proof in profile["benchmarks"]:
            report_path = Path(proof["report"])
            if file_hash(report_path) != proof["sha256"]:
                raise ValueError("The generation benchmark record changed.")
            report = read_json(report_path)
            if (
                report.get("status") != "complete"
                or not report.get("full_video_decode")
                or report.get("device") != self.device
                or report.get("workflow_sha256") != profile["workflow_sha256"]
                or file_hash(report["clip"]) != report["clip_sha256"]
            ):
                raise ValueError(
                    "A completed real clip and matching benchmark are required."
                )
            tested.append(settings_identity(dict(report["settings"], strength=1)))
        if not tested or any(
            settings_identity(s) not in tested for s in profile["tested_settings"]
        ):
            raise ValueError(
                "Every enabled setting must have a successful real benchmark."
            )
        if settings_identity(profile["default_settings"]) not in tested:
            raise ValueError(
                "Default settings also require a successful real benchmark."
            )
        for model in profile["models"]:
            weight = Path(model["path"])
            stat = weight.stat()
            if (
                _model_hash(str(weight), stat.st_size, stat.st_mtime_ns)
                != model["sha256"]
            ):
                raise ValueError(
                    "Configured model weights differ from the verified model: "
                    + weight.name
                )
        return profile

    def scope(self):
        profile = self.profile()
        return dict(
            workflow_sha256=profile["workflow_sha256"],
            profile_sha256=file_hash(self.keys["COMFY_PROFILE"]),
            model_fingerprint=digest(
                [{Path(m["path"]).name: m["sha256"]} for m in profile["models"]]
            ),
            execution_device=self.device,
            tested_settings=profile["tested_settings"],
            server_url=self.url,
            settings=FootageSettings.model_validate(
                profile["default_settings"]
            ).model_dump(),
            batch_enabled=bool(profile.get("batch_enabled")),
        )

    def validate_workflow(self, workflow, nodes):
        if not isinstance(workflow, dict) or not workflow:
            raise ValueError("Use an exported ComfyUI API graph.")
        by_type = {}
        for identifier, node in workflow.items():
            kind = node["class_type"]
            if kind not in self.LOCAL_NODES or kind not in nodes:
                raise ValueError("Missing or unverified local node: " + kind)
            by_type.setdefault(kind, []).append(identifier)
            for name, definition in (
                nodes[kind].get("input", {}).get("required", {}).items()
            ):
                options = definition[0]
                value = node.get("inputs", {}).get(name)
                if (
                    isinstance(value, str)
                    and value.startswith("__")
                    and value.endswith("__")
                ):
                    continue
                if (
                    isinstance(options, list)
                    and not isinstance(value, list)
                    and value not in options
                ):
                    raise ValueError("Unavailable model or enum: " + str(value))

        def single(kind):
            identifiers = by_type.get(kind, [])
            if len(identifiers) != 1:
                raise ValueError("Expected one verified " + kind)
            return identifiers[0]

        loader, conditioning = single("LoadImage"), single("LTXVImgToVideo")
        sampler, decoder, save = (
            single("SamplerCustom"),
            single("VAEDecode"),
            single("SaveWEBM"),
        )

        def inputs(identifier):
            return workflow[identifier]["inputs"]

        if (
            inputs(loader).get("image") != "__IMAGE__"
            or inputs(conditioning).get("image") != [loader, 0]
            or inputs(sampler).get("latent_image") != [conditioning, 2]
            or inputs(sampler).get("positive") != [conditioning, 0]
            or inputs(sampler).get("negative") != [conditioning, 1]
            or inputs(decoder).get("samples") != [sampler, 0]
            or inputs(save).get("images") != [decoder, 0]
        ):
            raise ValueError(
                "The uploaded image must condition the sampled latent and saved video; an unused loader or overlay is not image-to-video."
            )
        if inputs(sampler).get("noise_seed") != "__SEED__" or not any(
            n["class_type"] == "CLIPTextEncode"
            and n["inputs"].get("text") == "__PROMPT__"
            for n in workflow.values()
        ):
            raise ValueError("Bind the actual prompt and sampler seed.")
        for name, token in [
            ("width", "__WIDTH__"),
            ("height", "__HEIGHT__"),
            ("length", "__FRAMES__"),
        ]:
            if inputs(conditioning).get(name) != token:
                raise ValueError("Missing supported output binding: " + name)
        if (
            inputs(conditioning).get("batch_size") != 1
            or inputs(conditioning).get("strength") != 1
        ):
            raise ValueError(
                "Only the measured single-image, strength-one workflow is enabled."
            )
        timing, schedule = single("LTXVConditioning"), single("LTXVScheduler")
        encoder, model, vae = (
            single("CLIPLoaderGGUF"),
            single("UnetLoaderGGUF"),
            single("VAELoader"),
        )
        prompt_nodes = [
            i
            for i, n in workflow.items()
            if n["class_type"] == "CLIPTextEncode"
            and n["inputs"].get("text") == "__PROMPT__"
        ]
        if (
            len(prompt_nodes) != 1
            or inputs(prompt_nodes[0]).get("clip") != [encoder, 0]
            or inputs(timing).get("positive") != [prompt_nodes[0], 0]
            or inputs(conditioning).get("positive") != [timing, 0]
            or inputs(conditioning).get("negative") != [timing, 1]
            or inputs(conditioning).get("vae") != [vae, 0]
            or inputs(decoder).get("vae") != [vae, 0]
            or inputs(sampler).get("model") != [model, 0]
            or inputs(sampler).get("sigmas") != [schedule, 0]
            or inputs(schedule).get("steps") != "__STEPS__"
            or inputs(timing).get("frame_rate") != "__FPS__"
            or inputs(save).get("fps") != "__FPS__"
        ):
            raise ValueError(
                "Prompt, model, VAE, timing and schedule must feed the verified sampling path."
            )

    def readiness(self):
        try:
            profile = self.profile()
            stats, nodes = self.get("/system_stats"), self.get("/object_info")
            actual = stats.get("devices", [{}])[0]
            if actual.get("type") != self.device:
                raise ValueError(
                    "Server device does not match COMFY_DEVICE. CPU offloading is not CPU-only execution."
                )
            if self.device == "cpu" and "--cpu" not in stats["system"].get("argv", []):
                raise ValueError(
                    "Start the separate ComfyUI environment with python main.py --cpu."
                )
            if self.device == "cuda" and actual.get("name") != profile["device_name"]:
                raise ValueError(
                    "This GPU has not been measured with the configured workflow."
                )
            system = stats["system"]
            measured = read_json(profile["benchmarks"][0]["report"])["system"]["system"]
            if any(
                system.get(k) != measured.get(k)
                for k in ["comfyui_version", "pytorch_version"]
            ):
                raise ValueError(
                    "The inference software stack changed. Run a real benchmark before enabling it."
                )
            critical = {
                "--cpu",
                "--cpu-vae",
                "--fp32-vae",
                "--fp16-vae",
                "--bf16-vae",
                "--force-fp32",
                "--force-fp16",
                "--fp32-unet",
                "--fp16-unet",
                "--bf16-unet",
                "--fp32-text-enc",
                "--fp16-text-enc",
                "--bf16-text-enc",
                "--lowvram",
                "--novram",
                "--highvram",
                "--gpu-only",
                "--disable-dynamic-vram",
                "--use-pytorch-cross-attention",
            }

            def execution_flags(argv):
                flags = sorted(set(argv) & critical)
                if "--reserve-vram" in argv:
                    flags += ["--reserve-vram", argv[argv.index("--reserve-vram") + 1]]
                return flags

            if execution_flags(system.get("argv", [])) != execution_flags(
                measured.get("argv", [])
            ):
                raise ValueError(
                    "Execution flags differ from the measured profile. Use its recorded flags or benchmark the new configuration."
                )
            if system.get("ram_total", 0) < profile["minimum_ram_gb"] * 1024**3:
                raise ValueError("Insufficient system RAM for this measured profile.")
            if (
                system.get("ram_free", 0)
                < profile["minimum_available_ram_gb"] * 1024**3
            ):
                raise ValueError(
                    "Not enough available system RAM. Free memory before starting inference."
                )
            if self.device == "cuda" and (
                actual.get("vram_total", 0) < profile["minimum_vram_gb"] * 1024**3
                or actual.get("vram_free", 0)
                < profile["minimum_free_vram_gb"] * 1024**3
            ):
                raise ValueError(
                    "Available VRAM is outside the measured GPU profile; no compatibility claim is made."
                )
            self.validate_workflow(read_json(self.keys["COMFY_WORKFLOW"]), nodes)
            return dict(
                available=True,
                message="Measured local image-to-video profile is ready; product appearance still needs human review.",
                device=self.device,
                batch_enabled=bool(profile.get("batch_enabled")),
                stats=stats,
                scope=self.scope(),
            )
        except (
            requests.RequestException,
            ValueError,
            KeyError,
            OSError,
            TypeError,
        ) as error:
            return dict(available=False, message=str(error), device=self.device)

    def reconcile(self, marker):
        for identifier, record in self.get("/history").items():
            if marker in str(record.get("prompt", [])):
                return identifier
        queue = self.get("/queue")
        for row in queue.get("queue_running", []) + queue.get("queue_pending", []):
            if marker in str(row):
                return str(row[1])
        return None

    def _settle(self, task_key, cache, jobs):
        for entry in self.control.usage(self.cid):
            if (
                entry["key"].startswith(task_key + ":")
                and entry["status"] == "reserved"
            ):
                self.control.settle(
                    self.cid,
                    entry["key"],
                    charge({"generation_jobs": jobs}, cache.rates),
                )

    def cancel_queued(self):
        queue = self.get("/queue")
        owned = {
            t["provider_id"]: t
            for t in self.control.tasks(self.cid)
            if t["kind"] == "generation"
        }
        pending = [row[1] for row in queue.get("queue_pending", []) if row[1] in owned]
        if pending:
            response = requests.post(
                self.url + "/queue", json={"delete": pending}, timeout=(3, 15)
            )
            response.raise_for_status()
            after = self.get("/queue")
            still_present = {
                row[1]
                for row in after.get("queue_pending", [])
                + after.get("queue_running", [])
            }
            confirmed = []
            for identifier in pending:
                if identifier in still_present or self.get(
                    "/history/" + identifier
                ).get(identifier):
                    continue
                task = owned[identifier]
                self.control.task_update(
                    self.cid,
                    task["key"],
                    "cancelled",
                    error="Owned pending job removal confirmed; no running inference was interrupted.",
                )
                for entry in self.control.usage(self.cid):
                    if (
                        entry["key"].startswith(task["key"] + ":")
                        and entry["status"] == "reserved"
                    ):
                        # An accepted submission conservatively consumes one job,
                        # even when removed before execution. Never strand its reservation.
                        self.control.settle(
                            self.cid, entry["key"], json.loads(entry["reserved"])
                        )
                confirmed.append(identifier)
            return confirmed
        # The cancellation flag stops local polling/future work. Never interrupt an unrelated running job.
        return pending

    def build_workflow(self, scene):
        workflow = read_json(self.keys["COMFY_WORKFLOW"])
        edit = scene["edit"]
        replacements = {
            "__PROMPT__": edit["generation_prompt"],
            "__SEED__": edit["seed"],
        }
        conditioning_hash = ""
        if "__IMAGE__" in json.dumps(workflow):
            spec = FootageSettings.model_validate(edit["generation_settings"])
            profile = self.profile()
            if settings_identity(spec.model_dump()) not in [
                settings_identity(s) for s in profile["tested_settings"]
            ]:
                raise ValueError(
                    "These output settings have no real benchmark on this execution profile."
                )
            asset = self.assets.get(edit["conditioning_asset"], {})
            path = Path(asset.get("path", "__missing__"))
            if asset.get("use") != "identity" or not path.is_file():
                raise ValueError(
                    "Upload the selected product identity image before image-to-video generation."
                )
            conditioning_hash = file_hash(path)
            if conditioning_hash != self.brief.get("asset_hashes", {}).get(
                edit["conditioning_asset"]
            ):
                raise ValueError(
                    "Conditioning image bytes changed; fresh plan approval is required."
                )
            with path.open("rb") as source:
                response = requests.post(
                    self.url + "/upload/image",
                    files={"image": ("cfc-" + conditioning_hash + path.suffix, source)},
                    data={"type": "input", "overwrite": "false"},
                    timeout=(3, 30),
                )
            response.raise_for_status()
            uploaded = response.json()
            replacements.update(
                {
                    "__IMAGE__": "/".join(
                        filter(None, [uploaded.get("subfolder"), uploaded["name"]])
                    ),
                    "__WIDTH__": spec.width,
                    "__HEIGHT__": spec.height,
                    "__FRAMES__": spec.frames,
                    "__FPS__": spec.fps,
                    "__STEPS__": spec.steps,
                }
            )

        def replace(value):
            if isinstance(value, str):
                return replacements.get(value, value)
            if isinstance(value, dict):
                return {k: replace(v) for k, v in value.items()}
            if isinstance(value, list):
                return [replace(v) for v in value]
            return value

        return replace(workflow), conditioning_hash

    def verify_clip(self, path, settings=None):
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        probe = json.loads(result.stdout)
        video = next((s for s in probe["streams"] if s["codec_type"] == "video"), None)
        if not video or int(video.get("nb_read_frames", 0)) < 2:
            raise ValueError("Provider output is not a decoded video clip.")
        if settings and (
            video["width"],
            video["height"],
            int(video["nb_read_frames"]),
        ) != (settings["width"], settings["height"], settings["frames"]):
            raise ValueError(
                "Generated output dimensions or frame count differ from approved settings."
            )
        if settings:
            try:
                rate, duration = (
                    float(Fraction(video["avg_frame_rate"])),
                    float(probe["format"]["duration"]),
                )
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                raise ValueError("Generated frame timing is unreadable.") from None
            if (
                abs(rate - settings["fps"]) > 0.05
                or abs(duration - settings["frames"] / settings["fps"])
                > 1 / settings["fps"] + 0.03
            ):
                raise ValueError(
                    "Generated frame timing differs from approved settings."
                )
        run_ffmpeg(["-i", path, "-f", "null", "-"], Path(path).parent)
        return probe

    def generate(self, scene, key, cache):
        folder = cache.folder / "generation" / key
        folder.mkdir(parents=True, exist_ok=True)
        manifest = folder / "cache.json"
        task_key, marker = "generation:" + key, digest([self.cid, key])
        with self.control.exclusive(self.cid + ":" + task_key):
            self.control.check_cancelled(self.cid)
            if manifest.exists():
                saved = read_json(manifest)
                if all(
                    Path(p).is_file() and file_hash(p) == sha
                    for p, sha in saved["hashes"].items()
                ):
                    self.verify_clip(
                        saved["result"]["path"], saved["result"].get("settings")
                    )
                    self.last_result = saved["result"]
                    return self.last_result["path"]
            previous = self.control.task(self.cid, task_key)
            known_id = previous.get("provider_id", "") if previous else ""
            if previous and previous["status"] in ["failed", "cancelled", "paused"]:
                known_id = ""
            if (
                known_id.startswith("client:")
                or previous
                and previous["status"] in ["unknown", "submitting"]
            ):
                known_id = self.reconcile(marker)
                if not known_id:
                    raise RuntimeError(
                        "ComfyUI submission outcome remains unknown. Inspect its queue/history before permitting another submission."
                    )
                self.control.task_update(
                    self.cid, task_key, "queued", provider_id=known_id
                )
            if not known_id:
                ready = self.readiness()
                if not ready["available"]:
                    raise ValueError(ready["message"])
                self.control.task_start(
                    self.cid, task_key, "generation", max_attempts=2
                )
                attempt = self.control.task(self.cid, task_key)["attempts"]
                try:
                    self.control.reserve(
                        self.cid,
                        task_key + ":" + str(attempt),
                        charge({"generation_jobs": 1}, cache.rates),
                        cache.limits,
                        "generation",
                    )
                except ValueError as error:
                    self.control.task_update(
                        self.cid, task_key, "paused", error=str(error)
                    )
                    raise
                self.control.task_update(self.cid, task_key, "queued")
            started = time.monotonic()
            try:
                with self.control.resource(
                    "comfy", 1, self.cid, wait_seconds=self.timeout
                ):
                    if not known_id:
                        workflow, image_hash = self.build_workflow(scene)
                        self.control.check_cancelled(self.cid)
                        save_json(
                            folder / "submission.json",
                            dict(
                                marker=marker,
                                workflow=workflow,
                                conditioning_sha256=image_hash,
                            ),
                        )
                        self.control.task_update(
                            self.cid,
                            task_key,
                            "submitting",
                            provider_id="client:" + marker,
                        )
                        try:
                            response = requests.post(
                                self.url + "/prompt",
                                json={
                                    "prompt": workflow,
                                    "client_id": marker,
                                    "extra_data": {"cfc_task": marker},
                                },
                                timeout=(3, 30),
                            )
                        except requests.ConnectTimeout:
                            self.control.task_update(
                                self.cid,
                                task_key,
                                "failed",
                                provider_id="",
                                error="Connection was never established.",
                            )
                            self._settle(task_key, cache, 0)
                            raise
                        data = response.json()
                        if 400 <= response.status_code < 500 and not data.get(
                            "prompt_id"
                        ):
                            self.control.task_update(
                                self.cid,
                                task_key,
                                "failed",
                                provider_id="",
                                error="ComfyUI rejected the graph: " + str(data)[:350],
                            )
                            self._settle(task_key, cache, 0)
                            raise ValueError(
                                "ComfyUI rejected the submitted graph; inspect the recorded validation error."
                            )
                        response.raise_for_status()
                        if not data.get("prompt_id"):
                            raise RuntimeError(
                                "ComfyUI returned no provider ID; reconcile the submission before retrying."
                            )
                        known_id = str(data["prompt_id"])
                        self.control.task_update(
                            self.cid, task_key, "queued", provider_id=known_id
                        )
                        self.note("AI footage queued")
                    missing = 0
                    while time.monotonic() - started < self.timeout:
                        self.control.check_cancelled(self.cid)
                        record = self.get("/history/" + known_id).get(known_id, {})
                        if record.get("status", {}).get("status_str") == "error":
                            self._settle(task_key, cache, 1)
                            save_json(folder / "provider-error.json", record)
                            self.control.task_update(
                                self.cid,
                                task_key,
                                "failed",
                                error="Provider execution failed; see provider-error.json.",
                            )
                            raise RuntimeError(
                                "ComfyUI execution failed. Only this scene may be retried within the approved allowance."
                            )
                        if record.get("status", {}).get("completed"):
                            self._settle(task_key, cache, 1)
                            items = [
                                item
                                for node in record.get("outputs", {}).values()
                                for role in ["images", "videos", "gifs"]
                                for item in node.get(role, [])
                                if Path(item.get("filename", "")).suffix.lower()
                                in [".mp4", ".webm", ".mov"]
                            ]
                            if not items:
                                self.control.task_update(
                                    self.cid,
                                    task_key,
                                    "failed",
                                    error="Completed job produced no video.",
                                )
                                raise ValueError(
                                    "ComfyUI produced no video; no editing-only substitution is made."
                                )
                            item = items[0]
                            self.control.task_update(self.cid, task_key, "downloading")
                            response = requests.get(
                                self.url + "/view",
                                params={
                                    k: item[k]
                                    for k in ["filename", "subfolder", "type"]
                                    if k in item
                                },
                                stream=True,
                                timeout=(3, 30),
                            )
                            response.raise_for_status()
                            output = folder / (
                                "generated" + Path(item["filename"]).suffix.lower()
                            )
                            with output.open("wb") as target:
                                size = 0
                                for chunk in response.iter_content(1024 * 1024):
                                    self.control.check_cancelled(self.cid)
                                    size += len(chunk)
                                    if size > 200 * 1024 * 1024:
                                        raise ValueError(
                                            "Generated footage exceeds 200 MB."
                                        )
                                    target.write(chunk)
                            settings = scene["edit"].get("generation_settings")
                            try:
                                probe = self.verify_clip(output, settings)
                            except (
                                ValueError,
                                subprocess.SubprocessError,
                                RuntimeError,
                            ) as error:
                                self.control.task_update(
                                    self.cid,
                                    task_key,
                                    "waiting",
                                    error="Provider clip failed media validation.",
                                )
                                raise ValueError(
                                    "Downloaded clip failed validation; retry downloads the same provider result. "
                                    "Revise the scene for a new generation."
                                ) from error
                            submission = read_json(folder / "submission.json")
                            result = dict(
                                path=str(output),
                                provider_id=known_id,
                                source="generated footage",
                                generation_method="image-to-video",
                                conditioning_asset=scene["edit"].get(
                                    "conditioning_asset", ""
                                ),
                                conditioning_sha256=submission.get(
                                    "conditioning_sha256", ""
                                ),
                                settings=settings,
                                workflow_sha256=file_hash(self.keys["COMFY_WORKFLOW"]),
                                execution_device=self.device,
                                elapsed_seconds=round(time.monotonic() - started, 2),
                                probe=probe,
                            )
                            save_json(folder / "history.json", record)
                            save_json(
                                manifest,
                                dict(
                                    result=result,
                                    hashes={str(output): file_hash(output)},
                                ),
                            )
                            self.control.task_update(
                                self.cid, task_key, "complete", output=result
                            )
                            self.last_result = result
                            self.note("AI footage completed and decoded")
                            return str(output)
                        queue = self.get("/queue")
                        running = [row[1] for row in queue.get("queue_running", [])]
                        pending = [row[1] for row in queue.get("queue_pending", [])]
                        status = "generating" if known_id in running else "queued"
                        if known_id not in running + pending:
                            missing += 1
                            if missing >= 5:
                                self.control.task_update(
                                    self.cid,
                                    task_key,
                                    "unknown",
                                    error="Known ID disappeared from provider history/queue. Reconcile; never automatically resubmit.",
                                )
                                raise RuntimeError(
                                    "Known provider job is missing. Reconcile its saved ID before retrying."
                                )
                        else:
                            missing = 0
                        self.control.check_cancelled(self.cid)
                        if self.control.task(self.cid, task_key)["status"] != status:
                            self.control.task_update(self.cid, task_key, status)
                            self.note("AI footage " + status)
                        time.sleep(2)
                    self.control.task_update(
                        self.cid,
                        task_key,
                        "waiting",
                        error="Polling wait expired; the saved job may still run.",
                    )
                    raise RuntimeError(
                        "ComfyUI may still be running. Retry to poll this same provider ID; no new generation will be submitted."
                    )
            except BaseException as error:
                task = self.control.task(self.cid, task_key)
                if task["status"] == "submitting":
                    self.control.task_update(
                        self.cid, task_key, "unknown", error=str(error)
                    )
                elif known_id and task["status"] not in [
                    "failed",
                    "unknown",
                    "cancelled",
                ]:
                    self.control.task_update(
                        self.cid, task_key, "waiting", error=str(error)
                    )
                elif not known_id and task["status"] not in [
                    "failed",
                    "unknown",
                    "paused",
                ]:
                    self._settle(task_key, cache, 0)
                    self.control.task_update(
                        self.cid, task_key, "failed", provider_id="", error=str(error)
                    )
                raise
