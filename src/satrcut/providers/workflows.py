"""ComfyUI workflow templates.

`wan22-a14b` is the default graph the runner submits. `wan22-5b` and `ltx` are
alternate templates for the same HTTP provider. Identity locking (IP-Adapter or
PuLID) is a separate template: those nodes are custom and are not injected into
a render unless you ask for that template.
"""

from __future__ import annotations

from typing import Any

from satrcut.providers.comfyui_wan import workflow as wan_a14b
from satrcut.registry import IdentityLock


def wan_5b(shot: dict[str, Any], start_name: str, seed: int, prefix: str) -> dict[str, Any]:
    """Wan2.2 TI2V 5B image-to-video. Public weight names from the Comfy-Org Wan 2.2 repo."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": float(shot.get("shift") or 5.0)}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": shot.get("prompt") or ""}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": shot.get("negative") or ""}},
        "7": {"class_type": "LoadImage", "inputs": {"image": start_name}},
        "8": {"class_type": "Wan22ImageToVideoLatent", "inputs": {
            "vae": ["4", 0], "width": int(shot["width"]), "height": int(shot["height"]),
            "length": int(shot["frames"]), "batch_size": 1, "start_image": ["7", 0],
        }},
        "9": {"class_type": "KSampler", "inputs": {
            "model": ["2", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["8", 0],
            "seed": int(seed), "steps": int((shot.get("a14b") or {}).get("steps") or 20),
            "cfg": float((shot.get("a14b") or {}).get("cfg") or 1.0),
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
        }},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["4", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": prefix}},
    }


def ltx_video(shot: dict[str, Any], start_name: str, seed: int, prefix: str) -> dict[str, Any]:
    """LTX-Video image-to-video template.

    Class names follow the ComfyUI-LTXVideo node pack. Adjust them if your
    install uses a newer pack. This file is not submitted unless you pass
    `--workflow ltx`.
    """
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"}},
        "2": {"class_type": "LTXVConditioning", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "frame_rate": float(shot.get("fps") or 24)}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": shot.get("prompt") or ""}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": shot.get("negative") or ""}},
        "5": {"class_type": "LoadImage", "inputs": {"image": start_name}},
        "6": {"class_type": "LTXVImgToVideo", "inputs": {
            "positive": ["2", 0], "negative": ["2", 1], "vae": ["1", 2],
            "image": ["5", 0], "width": int(shot["width"]), "height": int(shot["height"]),
            "length": int(shot["frames"]), "strength": 1.0,
        }},
        "7": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["6", 0], "negative": ["6", 1], "latent_image": ["6", 2],
            "seed": int(seed), "steps": 30, "cfg": 3.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
        }},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }


def with_identity(graph: dict[str, Any], locks: list[IdentityLock | dict[str, Any]]) -> dict[str, Any]:
    """Add IP-Adapter or PuLID slots. The base sampler is left intact."""
    out = {key: value for key, value in graph.items()}
    for index, raw in enumerate(locks):
        if isinstance(raw, IdentityLock):
            method, sheet, weight, lora, character = raw.method, raw.sheet, raw.weight, raw.lora, raw.character
        else:
            method = str(raw.get("method") or "sheet")
            sheet = raw.get("sheet")
            weight = float(raw.get("weight") or 0.8)
            lora = raw.get("lora")
            character = str(raw.get("character") or "")
        if method in ("sheet", "none", ""):
            continue
        if method == "ipadapter":
            out[f"ipa_load_{index}"] = {
                "class_type": "IPAdapterModelLoader",
                "inputs": {"ipadapter_file": "ip-adapter-plus_sdxl_vit-h.safetensors"},
                "_satrcut": "optional custom node from ComfyUI_IPAdapter_plus; weights are yours",
            }
            out[f"ipa_{index}"] = {
                "class_type": "IPAdapterAdvanced",
                "inputs": {
                    "model": ["5", 0] if "5" in out else ["1", 0],
                    "ipadapter": [f"ipa_load_{index}", 0],
                    "image": sheet or "",
                    "weight": weight,
                    "weight_type": "linear",
                    "combine_embeds": "concat",
                    "start_at": 0.0,
                    "end_at": 1.0,
                    "embeds_scaling": "V only",
                },
                "_satrcut_character": character,
            }
        elif method == "pulid":
            out[f"pulid_{index}"] = {
                "class_type": "PuLIDApply",
                "inputs": {"model": ["5", 0] if "5" in out else ["1", 0], "image": sheet or "", "weight": weight, "method": "fidelity"},
                "_satrcut_character": character,
                "_satrcut": "optional PuLID custom node; not part of core ComfyUI",
            }
        elif method == "lora" and lora:
            out[f"id_lora_{index}"] = {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {"model": ["6", 0] if "6" in out else ["1", 0], "lora_name": lora, "strength_model": weight},
                "_satrcut_character": character,
            }
    return out


def build_graph(name: str, shot: dict[str, Any], start_name: str, end_name: str | None, seed: int, prefix: str) -> dict[str, Any]:
    key = (name or "wan22-a14b").lower()
    if key in ("wan22-a14b", "wan", "a14b", "comfyui"):
        graph = wan_a14b(shot, start_name, end_name, seed, prefix)
    elif key in ("wan22-5b", "wan5b", "5b"):
        graph = wan_5b(shot, start_name, seed, prefix)
    elif key in ("ltx", "ltx-video", "ltxvideo"):
        graph = ltx_video(shot, start_name, seed, prefix)
    elif key in ("wan22-a14b-identity", "identity"):
        graph = wan_a14b(shot, start_name, end_name, seed, prefix)
        graph = with_identity(graph, list(shot.get("identity_locks") or []))
    else:
        raise ValueError(f"unknown workflow template {name!r}")
    return graph


CAPABILITIES = [
    {"provider": "mock", "kind": "local", "keys": [], "video": True, "identity": "labeled stills", "offline": True, "workflow": None, "notes": "Default. No GPU, no key."},
    {"provider": "comfyui", "kind": "local", "keys": [], "video": True, "identity": "keyframes, optional IP-Adapter/PuLID/LoRA template", "offline": True, "workflow": "wan22-a14b", "notes": "HTTP only. ComfyUI is not vendored."},
    {"provider": "comfyui", "kind": "local", "keys": [], "video": True, "identity": "start frame", "offline": True, "workflow": "wan22-5b", "notes": "Wan2.2 TI2V 5B template."},
    {"provider": "comfyui", "kind": "local", "keys": [], "video": True, "identity": "start frame", "offline": True, "workflow": "ltx", "notes": "LTX-Video template. Needs the LTX node pack."},
    {"provider": "fal", "kind": "cloud", "keys": ["FAL_KEY"], "video": True, "identity": "image URL", "offline": False, "workflow": None, "notes": "Optional. No call unless FAL_KEY is set."},
    {"provider": "replicate", "kind": "cloud", "keys": ["REPLICATE_API_TOKEN"], "video": True, "identity": "image URL", "offline": False, "workflow": None, "notes": "Optional. No call unless the token is set."},
]
