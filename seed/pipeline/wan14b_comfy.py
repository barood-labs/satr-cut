"""Optional higher-quality Stage 1: Wan2.2 I2V-A14B (fp8_scaled high+low noise experts) with the
lightx2v 4-step distill LoRAs, native ComfyUI nodes only (Comfy-Org/Wan-2.2 @ ee6f4a4, Apache-2.0).
Fits a 20 GB card (ComfyUI keeps one 14 GB expert on the GPU at a time). Native 16 fps: 81 frames = 5.06 s.
Shot fields used: prompt, negative, width, height, frames (4n+1), fps(16), a14b {steps, split, shift, cfg, lora}.
Same render()/output contract as wan_comfy.render (frames -> mp4)."""
import shutil
from pathlib import Path
import wan_comfy

DEF = {"steps": 8, "split": 4, "shift": 5.0, "cfg": 1.0, "lora": 1.0, "sampler": "euler", "scheduler": "simple"}

def workflow(shot, image_name, seed, prefix):
    p = dict(DEF, **shot.get("a14b", {}))
    # v3 motion presets (2026-11-14): high_lora / high_cfg / low_cfg let the high-noise expert (which lays out the motion)
    # run WITHOUT the lightx2v distill LoRA at real cfg, while the low-noise expert keeps the fast LoRA at cfg 1.
    hl = p.get("high_lora", p["lora"]); hc = p.get("high_cfg", p["cfg"]); lc = p.get("low_cfg", p["cfg"])
    use_lora = p["lora"] > 0
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", "strength_model": hl if hl > 0 else p["lora"]}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", "strength_model": p["lora"]}},
        "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0] if (use_lora and hl > 0) else ["1", 0], "shift": p["shift"]}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0] if use_lora else ["2", 0], "shift": p["shift"]}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan"}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": shot["prompt"]}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": shot["negative"]}},
        "11": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "12": {"class_type": "WanImageToVideo", "inputs": {"positive": ["9", 0], "negative": ["10", 0], "vae": ["8", 0],
               "width": shot["width"], "height": shot["height"], "length": shot["frames"], "batch_size": 1, "start_image": ["11", 0]}},
        "13": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["5", 0], "add_noise": "enable", "noise_seed": seed,
               "steps": p["steps"], "cfg": hc, "sampler_name": p["sampler"], "scheduler": p["scheduler"],
               "positive": ["12", 0], "negative": ["12", 1], "latent_image": ["12", 2],
               "start_at_step": 0, "end_at_step": p["split"], "return_with_leftover_noise": "enable"}},
        "14": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["6", 0], "add_noise": "disable", "noise_seed": seed,
               "steps": p["steps"], "cfg": lc, "sampler_name": p["sampler"], "scheduler": p["scheduler"],
               "positive": ["12", 0], "negative": ["12", 1], "latent_image": ["13", 0],
               "start_at_step": p["split"], "end_at_step": 10000, "return_with_leftover_noise": "disable"}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["8", 0]}},
        "11b": None,
    }
    wf.pop("11b")
    wf["16"] = {"class_type": "SaveImage", "inputs": {"images": ["15", 0], "filename_prefix": prefix}}
    if not use_lora:
        wf.pop("3"); wf.pop("4")
    elif hl <= 0:
        wf.pop("3")
    # character LoRAs (musubi-tuner, trained on the low-noise expert): shot["char_loras"] = [{"name": ..., "strength": 0.8, "high": 0}]
    lo, hi = wf["6"]["inputs"]["model"], wf["5"]["inputs"]["model"]
    for k, cl in enumerate(shot.get("char_loras", [])):
        wf[f"cl{k}"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": lo, "lora_name": cl["name"], "strength_model": cl.get("strength", 0.8)}}
        lo = [f"cl{k}", 0]
        if cl.get("high", 0) > 0:
            wf[f"ch{k}"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": hi, "lora_name": cl["name"], "strength_model": cl["high"]}}
            hi = [f"ch{k}", 0]
    wf["6"]["inputs"]["model"], wf["5"]["inputs"]["model"] = lo, hi
    return wf

def render(comfy, port, shot, seed, out_dir, start_png, timeout_s=4 * 3600):
    """wan_comfy.render with this graph (its output node id is '11'; ours is '16')."""
    orig = wan_comfy.workflow
    def wf(shot_, image_name, seed_, prefix):
        g = workflow(shot_, image_name, seed_, prefix)
        g["11"], g["11_img"] = g.pop("16"), g.pop("11")          # SaveImage must be node "11" for render()
        g["11"]["inputs"]["images"] = ["15", 0]
        g["12"]["inputs"]["start_image"] = ["11_img", 0]
        return g
    end_png = Path(start_png).parent / f"end_{shot['id']}.png"   # first+last-frame mode (2-09 lesson): job carries end_<id>.png
    if end_png.exists():
        shutil.copy(end_png, Path(comfy) / "input" / end_png.name)
        base_wf = wf
        def wf(shot_, image_name, seed_, prefix):
            g = base_wf(shot_, image_name, seed_, prefix)
            g["12"]["class_type"] = "WanFirstLastFrameToVideo"
            g["11_end"] = {"class_type": "LoadImage", "inputs": {"image": end_png.name}}
            g["12"]["inputs"]["end_image"] = ["11_end", 0]
            return g
    wan_comfy.workflow = wf
    try:
        return wan_comfy.render(comfy, port, shot, seed, out_dir, start_png, timeout_s)
    finally:
        wan_comfy.workflow = orig
