"""Optional local ComfyUI footage adapter using its documented HTTP queue API."""

from pathlib import Path
from urllib.parse import urlparse
import json
import time
import requests
from .storage import read_json, digest
from .rendering.editor import run_ffmpeg


class ComfyFootage:
    LOCAL_NODES = {
        "UNETLoader",
        "CLIPLoader",
        "DualCLIPLoader",
        "VAELoader",
        "CLIPTextEncode",
        "EmptyHunyuanLatentVideo",
        "EmptyLatentVideo",
        "ModelSamplingSD3",
        "ModelSamplingAuraFlow",
        "KSampler",
        "KSamplerAdvanced",
        "VAEDecode",
        "CreateVideo",
        "SaveVideo",
        "VHS_VideoCombine",
    }

    def __init__(self, keys, control, cid, gpu_workers=1):
        self.keys, self.control, self.cid, self.gpu_workers = (
            keys,
            control,
            cid,
            gpu_workers,
        )
        self.url = (keys.get("COMFY_URL") or "http://127.0.0.1:8188").rstrip("/")
        parsed = urlparse(self.url)
        if parsed.scheme not in ["http", "https"] or parsed.hostname not in [
            "localhost",
            "127.0.0.1",
            "::1",
        ]:
            raise ValueError(
                "This adapter supports a local ComfyUI server only; cloud/paid endpoints are not enabled."
            )

    def get(self, route, **params):
        response = requests.get(self.url + route, params=params, timeout=(3, 15))
        response.raise_for_status()
        return response.json()

    def readiness(self):
        path = self.keys.get("COMFY_WORKFLOW")
        if not path or not Path(path).is_file():
            return dict(
                available=False,
                message="No local ComfyUI API workflow configured. Image-based motion graphics remain available.",
            )
        try:
            stats = self.get("/system_stats")
            nodes = self.get("/object_info")
            workflow = read_json(path)
            for node in workflow.values():
                if node["class_type"] not in self.LOCAL_NODES:
                    raise ValueError(
                        "This demo has not verified the node as local-only: "
                        + node["class_type"]
                    )
                if node["class_type"] not in nodes:
                    raise ValueError(
                        "Workflow requires a missing ComfyUI node: "
                        + node["class_type"]
                    )
                schema = nodes[node["class_type"]].get("input", {}).get("required", {})
                for name, definition in schema.items():
                    options = definition[0]
                    value = node.get("inputs", {}).get(name)
                    if (
                        isinstance(options, list)
                        and not isinstance(value, list)
                        and value not in options
                    ):
                        raise ValueError("Model or enum is unavailable: " + str(value))
            minimum = float(self.keys.get("COMFY_MIN_VRAM_GB") or 10) * 1024**3
            if not any(
                device.get("vram_total", 0) >= minimum
                for device in stats.get("devices", [])
            ):
                raise ValueError(
                    "Configured workflow hardware allowance is unavailable; use image-based mode."
                )
            return dict(
                available=True,
                message="Local server, workflow nodes, model selections and declared VRAM requirement verified.",
                workflow_hash=digest(workflow),
                stats=stats,
            )
        except (requests.RequestException, ValueError, KeyError) as error:
            return dict(available=False, message=str(error))

    def reconcile(self, marker):
        for identifier, record in self.get("/history").items():
            if marker in str(record.get("prompt", [])):
                return identifier
        queue = self.get("/queue")
        for row in queue.get("queue_running", []) + queue.get("queue_pending", []):
            if marker in str(row):
                return str(row[1])
        return None

    def cancel_queued(self):
        queue = self.get("/queue")
        owned = {
            task["provider_id"]
            for task in self.control.tasks(self.cid)
            if task["kind"] == "generation"
        }
        pending = [row[1] for row in queue.get("queue_pending", []) if row[1] in owned]
        if pending:
            response = requests.post(
                self.url + "/queue", json={"delete": pending}, timeout=(3, 15)
            )
            response.raise_for_status()
        return pending

    def generate(self, scene, key, cache):
        task_key = "generation:" + key
        previous = self.control.task(self.cid, task_key)
        marker = digest([self.cid, key])
        known_id = previous.get("provider_id", "") if previous else ""
        if (
            known_id.startswith("client:")
            or previous
            and previous["status"] == "unknown"
        ):
            known_id = self.reconcile(marker)
            if not known_id:
                raise RuntimeError(
                    "ComfyUI submission outcome remains unknown. Check its queue/history before permitting a new submission."
                )
            self.control.task_update(
                self.cid, task_key, "running", provider_id=known_id
            )

        def build(folder):
            nonlocal known_id
            ready = self.readiness()
            if not ready["available"]:
                raise ValueError(ready["message"])
            with self.control.resource("gpu", self.gpu_workers, self.cid):
                if not known_id:
                    workflow = read_json(self.keys["COMFY_WORKFLOW"])

                    def replace(value):
                        if value == "__PROMPT__":
                            return scene["edit"]["generation_prompt"]
                        if value == "__SEED__":
                            return scene["edit"]["seed"]
                        if isinstance(value, dict):
                            return {k: replace(v) for k, v in value.items()}
                        if isinstance(value, list):
                            return [replace(v) for v in value]
                        return value

                    self.control.task_update(
                        self.cid, task_key, "submitting", provider_id="client:" + marker
                    )
                    self.control.check_cancelled(self.cid)
                    response = requests.post(
                        self.url + "/prompt",
                        json={
                            "prompt": replace(workflow),
                            "client_id": marker,
                            "extra_data": {"cfc_task": marker},
                        },
                        timeout=(3, 30),
                    )
                    response.raise_for_status()
                    data = response.json()
                    if data.get("node_errors") or not data.get("prompt_id"):
                        raise ValueError("ComfyUI rejected the configured workflow.")
                    known_id = data["prompt_id"]
                    self.control.task_update(
                        self.cid, task_key, "running", provider_id=known_id
                    )
                for _ in range(600):
                    self.control.check_cancelled(self.cid)
                    record = self.get("/history/" + known_id).get(known_id, {})
                    if record.get("status", {}).get("status_str") == "error":
                        raise RuntimeError(
                            "ComfyUI execution failed; inspect its recorded provider job."
                        )
                    outputs = record.get("outputs", {})
                    for node in outputs.values():
                        for item in (
                            node.get("videos", [])
                            + node.get("gifs", [])
                            + node.get("images", [])
                        ):
                            if Path(item.get("filename", "")).suffix.lower() not in [
                                ".mp4",
                                ".webm",
                                ".mov",
                            ]:
                                continue
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
                            output = folder / "generated.mp4"
                            with output.open("wb") as target:
                                size = 0
                                for chunk in response.iter_content(1024 * 1024):
                                    size += len(chunk)
                                    if size > 200 * 1024 * 1024:
                                        raise ValueError(
                                            "Generated footage exceeds the 200 MB demo limit."
                                        )
                                    target.write(chunk)
                            return dict(
                                path=str(output),
                                provider_id=known_id,
                                source="generated footage",
                                _files=[output],
                            )
                    time.sleep(1)
                raise RuntimeError(
                    "ComfyUI is still running; reconcile its provider job before retrying."
                )

        def verify(result):
            run_ffmpeg(
                ["-i", result["path"], "-frames:v", 1, "-f", "null", "-"],
                Path(result["path"]).parent,
            )

        if (
            not (cache.folder / "generation" / key / "cache.json").exists()
            and not known_id
        ):
            ready = self.readiness()
            if not ready["available"]:
                raise ValueError(ready["message"])
        result, _ = cache.run(
            "generation",
            key,
            build,
            verify,
            {"generation_jobs": 0 if known_id else 1},
            external=True,
        )
        # A recovered job consumes the original submission reservation, not a
        # second generation credit for polling/downloading its existing output.
        for entry in self.control.usage(self.cid):
            if (
                entry["key"].startswith(task_key + ":")
                and entry["status"] == "reserved"
            ):
                self.control.settle(
                    self.cid, entry["key"], json.loads(entry["reserved"])
                )
        return result["path"]
