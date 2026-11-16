"""ComfyUI client for Wan2.2 I2V A14B.

Talks to a local server over HTTP (`/prompt`, `/history`, `/view`, `/system_stats`).
ComfyUI itself is GPL-3.0 and is not vendored. Weights stay on the operator's
machine. The public sha256 pins below are checksums, not secrets. Downloads,
when someone runs them by hand, use a public Hugging Face URL and no token.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from satrcut.errors import InfraError
from satrcut.providers.base import RenderRequest, RenderResult
from satrcut.util import sha256_file, x264_preset

COMFY_COMMIT = "2c75dc1b379a59e0880619d029607af5169a62ef"
WAN_REPO = "Comfy-Org/Wan-2.2"
WAN_REV = "ee6f4a40737a995bf5818954cfce6d59443b0f04"
# Public weight checksums for the 5B TI2V path. A14B filenames are the graph below.
WEIGHTS = [
    ("diffusion_models", "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", 9999658848,
     "456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e"),
    ("text_encoders", "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", 6735906897,
     "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68"),
    ("vae", "split_files/vae/wan2.2_vae.safetensors", 1409400960,
     "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156"),
]
A14B_FILES = (
    "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
    "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
    "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
    "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "wan_2.1_vae.safetensors",
)
PRESET = {"steps": 8, "split": 4, "shift": 5.0, "cfg": 1.0, "lora": 1.0, "sampler": "euler", "scheduler": "simple"}


def comfy_url() -> str:
    return os.environ.get("SATRCUT_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")


def comfy_root() -> Path | None:
    raw = os.environ.get("SATRCUT_COMFY_ROOT")
    return Path(raw).expanduser() if raw else None


def server_command(port: int) -> list[str]:
    return [
        os.environ.get("SATRCUT_COMFY_PYTHON") or "python",
        "main.py",
        "--listen",
        "127.0.0.1",
        "--port",
        str(port),
        "--disable-auto-launch",
    ]


def workflow(shot: dict[str, Any], start_name: str, end_name: str | None, seed: int, prefix: str) -> dict[str, Any]:
    """Wan2.2 I2V A14B graph from seed/pipeline/wan14b_comfy.py, including first+last frame."""
    preset = dict(PRESET)
    preset.update(shot.get("a14b") or {})
    high_lora = preset.get("high_lora", preset["lora"])
    high_cfg = preset.get("high_cfg", preset["cfg"])
    low_cfg = preset.get("low_cfg", preset["cfg"])
    use_lora = float(preset["lora"]) > 0
    graph: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", "strength_model": high_lora if high_lora > 0 else preset["lora"]}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", "strength_model": preset["lora"]}},
        "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0] if (use_lora and high_lora > 0) else ["1", 0], "shift": preset["shift"]}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0] if use_lora else ["2", 0], "shift": preset["shift"]}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan"}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": shot["prompt"]}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": shot.get("negative") or ""}},
        "11": {"class_type": "LoadImage", "inputs": {"image": start_name}},
        "12": {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["9", 0], "negative": ["10", 0], "vae": ["8", 0],
            "width": int(shot["width"]), "height": int(shot["height"]), "length": int(shot["frames"]),
            "batch_size": 1, "start_image": ["11", 0],
        }},
        "13": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["5", 0], "add_noise": "enable", "noise_seed": int(seed),
            "steps": preset["steps"], "cfg": high_cfg, "sampler_name": preset["sampler"], "scheduler": preset["scheduler"],
            "positive": ["12", 0], "negative": ["12", 1], "latent_image": ["12", 2],
            "start_at_step": 0, "end_at_step": preset["split"], "return_with_leftover_noise": "enable",
        }},
        "14": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["6", 0], "add_noise": "disable", "noise_seed": int(seed),
            "steps": preset["steps"], "cfg": low_cfg, "sampler_name": preset["sampler"], "scheduler": preset["scheduler"],
            "positive": ["12", 0], "negative": ["12", 1], "latent_image": ["13", 0],
            "start_at_step": preset["split"], "end_at_step": 10000, "return_with_leftover_noise": "disable",
        }},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["8", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["15", 0], "filename_prefix": prefix}},
    }
    if not use_lora:
        graph.pop("3")
        graph.pop("4")
    elif high_lora <= 0:
        graph.pop("3")
    low_model, high_model = graph["6"]["inputs"]["model"], graph["5"]["inputs"]["model"]
    for index, lora in enumerate(shot.get("char_loras") or []):
        graph[f"cl{index}"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": low_model, "lora_name": lora["name"], "strength_model": lora.get("strength", 0.8)}}
        low_model = [f"cl{index}", 0]
        if lora.get("high", 0) > 0:
            graph[f"ch{index}"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": high_model, "lora_name": lora["name"], "strength_model": lora["high"]}}
            high_model = [f"ch{index}", 0]
    graph["6"]["inputs"]["model"] = low_model
    graph["5"]["inputs"]["model"] = high_model
    if end_name:
        graph["12"]["class_type"] = "WanFirstLastFrameToVideo"
        graph["11_end"] = {"class_type": "LoadImage", "inputs": {"image": end_name}}
        graph["12"]["inputs"]["end_image"] = ["11_end", 0]
    return graph


def _request(url: str, data: bytes | None = None, timeout: float = 30) -> Any:
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
    except urllib.error.URLError as exc:
        raise InfraError(f"ComfyUI request failed: {exc}") from exc
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def healthy(url: str | None = None, timeout: float = 2) -> bool:
    try:
        _request(f"{url or comfy_url()}/system_stats", timeout=timeout)
        return True
    except InfraError:
        return False


def _upload(url: str, path: Path) -> str:
    """POST /upload/image as multipart. Returns the name ComfyUI stored."""
    boundary = "----satrcut" + uuid.uuid4().hex
    filename = path.name
    payload = path.read_bytes()
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{url}/upload/image",
        data=head + payload + tail,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise InfraError(f"ComfyUI upload failed: {exc}") from exc
    return str(body.get("name") or filename)


def _view(url: str, image: dict[str, Any], dest: Path) -> None:
    query = urllib.parse.urlencode(
        {"filename": image["filename"], "subfolder": image.get("subfolder") or "", "type": image.get("type") or "output"}
    )
    try:
        with urllib.request.urlopen(f"{url}/view?{query}", timeout=120) as response:
            dest.write_bytes(response.read())
    except urllib.error.URLError as exc:
        raise InfraError(f"ComfyUI view failed: {exc}") from exc


def start_server(root: Path, port: int, logfile: Path) -> subprocess.Popen[bytes]:
    logfile.parent.mkdir(parents=True, exist_ok=True)
    handle = logfile.open("w", encoding="utf-8")
    process = subprocess.Popen(server_command(port), cwd=root, stdout=handle, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        if healthy(url, timeout=2):
            return process
        if process.poll() is not None:
            raise InfraError(f"ComfyUI exited; see {logfile}")
        time.sleep(2)
    process.terminate()
    raise InfraError("ComfyUI did not open /system_stats")


def hf_download(repo: str, revision: str, remote_path: str, dest: Path, expect_sha: str | None = None, expect_size: int | None = None) -> Path:
    """Download one public file. No Authorization header and no token."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (expect_size is None or dest.stat().st_size == expect_size):
        if expect_sha is None or sha256_file(dest) == expect_sha:
            return dest
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{remote_path}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    subprocess.run(["curl", "-L", "--fail", "--retry", "5", "-o", str(tmp), url], check=True)
    if expect_size is not None and tmp.stat().st_size != expect_size:
        raise RuntimeError(f"size mismatch for {dest.name}")
    if expect_sha is not None and sha256_file(tmp) != expect_sha:
        raise RuntimeError(f"sha256 mismatch for {dest.name}")
    tmp.replace(dest)
    return dest


class ComfyUIWanProvider:
    name = "comfyui"

    def __init__(self, url: str | None = None) -> None:
        self.url = (url or comfy_url()).rstrip("/")
        self._process: subprocess.Popen[bytes] | None = None

    def healthy(self) -> bool:
        return healthy(self.url)

    def restart(self) -> None:
        root = comfy_root()
        if root is None:
            raise InfraError("ComfyUI is not answering and SATRCUT_COMFY_ROOT is unset")
        port = urllib.parse.urlparse(self.url).port or 8188
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
        log_path = Path(os.environ.get("SATRCUT_PROJECT") or ".").resolve() / ".satrcut" / "comfyui.log"
        self._process = start_server(root, port, log_path)
        self.url = f"http://127.0.0.1:{port}"

    def render(self, request: RenderRequest) -> RenderResult:
        if not self.healthy():
            self.restart()
        shot = dict(request.shot)
        shot["a14b"] = dict(PRESET)
        shot["a14b"].update(request.preset)
        start_name = _upload(self.url, request.start_frame)
        end_name = _upload(self.url, request.end_frame) if request.end_frame.exists() else None
        prefix = f"satrcut_{shot['id']}_s{request.seed}"
        from satrcut.providers.workflows import build_graph

        graph = build_graph(str(shot.get("workflow") or "wan22-a14b"), shot, start_name, end_name, request.seed, prefix)
        request.out_dir.mkdir(parents=True, exist_ok=True)
        (request.out_dir / "workflow.json").write_text(json.dumps(graph, indent=2), encoding="utf-8")
        body = json.dumps({"prompt": graph, "client_id": uuid.uuid4().hex}).encode()
        queued = _request(f"{self.url}/prompt", body, timeout=60)
        if queued.get("error"):
            raise RuntimeError("ComfyUI rejected the prompt: " + json.dumps(queued["error"])[:1500])
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            raise InfraError("ComfyUI /prompt returned no prompt_id")
        timeout_s = float(os.environ.get("SATRCUT_RENDER_TIMEOUT", str(4 * 3600)))
        poll = float(os.environ.get("SATRCUT_COMFY_POLL", "5"))
        started = time.time()
        history: dict[str, Any] | None = None
        while True:
            if time.time() - started > timeout_s:
                raise InfraError("Wan render timed out")
            time.sleep(poll)
            try:
                payload = _request(f"{self.url}/history/{prompt_id}", timeout=30)
            except InfraError:
                if not self.healthy():
                    raise
                continue
            if prompt_id in payload:
                status = payload[prompt_id].get("status") or {}
                if status.get("completed") or status.get("status_str") in ("success", "error"):
                    history = payload
                    break
        assert history is not None
        status = history[prompt_id]["status"]
        if status.get("status_str") != "success":
            raise RuntimeError("Wan render failed: " + json.dumps(status)[:2000])
        images = history[prompt_id]["outputs"]["16"]["images"]
        frames = request.out_dir / "frames"
        frames.mkdir(exist_ok=True)
        for index, image in enumerate(sorted(images, key=lambda item: item["filename"])):
            _view(self.url, image, frames / f"{index:05d}.png")
        mp4 = request.out_dir / f"wan_{shot['id']}_s{request.seed}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-framerate", str(shot.get("fps") or 16),
                "-i", str(frames / "%05d.png"),
                "-c:v", "libx264", "-preset", x264_preset(), "-crf", "16", "-pix_fmt", "yuv420p",
                str(mp4),
            ],
            check=True,
        )
        for frame in frames.glob("*.png"):
            frame.unlink()
        frames.rmdir()
        return RenderResult(mp4=mp4, seed=request.seed, render_s=round(time.time() - started, 1), backend=self.name)


def describe_models() -> str:
    lines = ["Wan2.2 files the ComfyUI provider expects. Checksums are public pins, not secrets.", ""]
    lines.append("A14B I2V (this provider's graph):")
    lines.extend(f"  {name}" for name in A14B_FILES)
    lines.append("")
    lines.append(f"5B TI2V reference pins ({WAN_REPO} @ {WAN_REV}):")
    for folder, remote, size, digest in WEIGHTS:
        lines.append(f"  {folder}/{Path(remote).name}  size={size}  sha256={digest}")
    lines.append("")
    lines.append("The tool does not download weights. A gated repo needs HF_TOKEN in the environment, never in the plan.")
    return "\n".join(lines)
