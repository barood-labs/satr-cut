"""Talking-shot generator: InfiniteTalk (MeiGen, Apache-2.0) on Wan2.1-I2V-14B-480P fp8 (Apache-2.0) with the
lightx2v 6-step distill LoRA (Apache-2.0) -- native ComfyUI v0.44 nodes only (WanInfiniteTalkToVideo), settings
mirror ComfyUI's official template video_wan2_1_infinitetalk (euler, normal, 6 steps, cfg 1, zeroed negative,
81-frame windows, 9 motion frames). Image + padded WAV -> 25 fps clip whose mouth is driven by the audio, so no
separate lip-sync pass. Longer than 81 frames = chained windows (previous_frames). Output trimmed to shot frames.
Weights: diffusion_models/wan2.1_i2v_480p_14B_fp8_scaled, model_patches/wan2.1_infiniteTalk_single_fp16,
loras/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64, audio_encoders/wav2vec2-chinese-base_fp16 (MIT),
text_encoders/umt5_xxl_fp8_e4m3fn_scaled, vae/wan_2.1_vae."""
import json, math, shutil, time, urllib.request, uuid
from pathlib import Path
from common import log, sh

WIN, MOTION = 81, 9

def workflow(shot, image_name, audio_name, seed, prefix):
    p = dict({"steps": 6, "audio_scale": 1.0, "lora": 1.0}, **shot.get("infinitetalk", {}))
    n_win = max(1, math.ceil((shot["frames"] - WIN) / (WIN - MOTION)) + 1)
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.1_i2v_480p_14B_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors", "strength_model": p["lora"]}},
        "3": {"class_type": "ModelPatchLoader", "inputs": {"name": "wan2.1_infiniteTalk_single_fp16.safetensors"}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan"}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 0], "text": shot["prompt"]}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "8": {"class_type": "AudioEncoderLoader", "inputs": {"audio_encoder_name": "wav2vec2-chinese-base_fp16.safetensors"}},
        "9": {"class_type": "LoadAudio", "inputs": {"audio": audio_name}},
        "10": {"class_type": "AudioEncoderEncode", "inputs": {"audio_encoder": ["8", 0], "audio": ["9", 0]}},
        "11": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "12": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
    }
    prev = None
    for w in range(n_win):
        b = 100 + 20 * w; B = lambda k: str(b + k)
        it = {"mode": "single_speaker", "model": ["2", 0], "model_patch": ["3", 0], "positive": ["5", 0], "negative": ["6", 0],
              "vae": ["7", 0], "width": shot["width"], "height": shot["height"], "length": WIN, "start_image": ["11", 0],
              "audio_encoder_output_1": ["10", 0], "motion_frame_count": MOTION, "audio_scale": p["audio_scale"]}
        if prev: it["previous_frames"] = prev
        g[B(0)] = {"class_type": "WanInfiniteTalkToVideo", "inputs": it}
        g[B(1)] = {"class_type": "BasicScheduler", "inputs": {"model": [B(0), 0], "scheduler": "normal", "steps": p["steps"], "denoise": 1.0}}
        g[B(2)] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed + w}}
        g[B(3)] = {"class_type": "CFGGuider", "inputs": {"model": [B(0), 0], "positive": [B(0), 1], "negative": [B(0), 2], "cfg": 1.0}}
        g[B(4)] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": [B(2), 0], "guider": [B(3), 0], "sampler": ["12", 0],
                                                                       "sigmas": [B(1), 0], "latent_image": [B(0), 3]}}
        g[B(5)] = {"class_type": "VAEDecode", "inputs": {"samples": [B(4), 0], "vae": ["7", 0]}}
        if prev is None:
            cur = [B(5), 0]
        else:
            g[B(6)] = {"class_type": "ImageFromBatch", "inputs": {"image": [B(5), 0], "batch_index": [B(0), 4], "length": 4096}}
            g[B(7)] = {"class_type": "ImageBatch", "inputs": {"image1": prev, "image2": [B(6), 0]}}
            cur = [B(7), 0]
        prev = cur
    g["99"] = {"class_type": "SaveImage", "inputs": {"images": prev, "filename_prefix": prefix}}
    return g

def render(comfy, port, shot, seed, out_dir, start_png, audio_wav, timeout_s=4 * 3600):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    img, aud = f"start_{shot['id']}.png", f"audio_{shot['id']}_it.wav"
    shutil.copy(start_png, Path(comfy) / "input" / img); shutil.copy(audio_wav, Path(comfy) / "input" / aud)
    prefix = f"satrcut_it_{shot['id']}_s{seed}"
    wf = workflow(shot, img, aud, seed, prefix)
    (out_dir / "workflow.json").write_text(json.dumps(wf, indent=1, ensure_ascii=False), encoding="utf-8")
    body = json.dumps({"prompt": wf, "client_id": uuid.uuid4().hex}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/prompt", data=body, headers={"Content-Type": "application/json"})
    pid = json.loads(urllib.request.urlopen(req).read())["prompt_id"]; t0 = time.time(); log("queued", pid)
    while True:
        time.sleep(15)
        h = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/history/{pid}").read())
        if pid in h and (h[pid].get("status", {}).get("completed") or h[pid].get("status", {}).get("status_str") in ("success", "error")):
            break
        if time.time() - t0 > timeout_s: raise RuntimeError("InfiniteTalk render timed out")
    secs = time.time() - t0
    (out_dir / "comfy-history.json").write_text(json.dumps(h, indent=1, ensure_ascii=False), encoding="utf-8")
    if h[pid]["status"].get("status_str") != "success":
        raise RuntimeError("InfiniteTalk failed: " + json.dumps(h[pid]["status"])[:2000])
    imgs = sorted(h[pid]["outputs"]["99"]["images"], key=lambda x: x["filename"])
    fd = out_dir / "frames"; fd.mkdir(exist_ok=True)
    for i, im in enumerate(imgs):
        src = Path(comfy) / "output" / im.get("subfolder", "") / im["filename"]
        if i < shot["frames"]: shutil.move(src, fd / f"{i:05d}.png")
        else: src.unlink()
    mp4 = out_dir / f"wan_{shot['id']}_s{seed}_{shot['width']}x{shot['height']}_{shot['fps']}fps.mp4"
    sh(["ffmpeg", "-y", "-v", "error", "-framerate", str(shot["fps"]), "-i", str(fd / "%05d.png"),
        "-c:v", "libx264", "-preset", "slow", "-crf", "12", "-pix_fmt", "yuv420p", str(mp4)])
    log(f"InfiniteTalk {shot['id']} seed {seed}: {min(len(imgs), shot['frames'])}/{len(imgs)} frames in {secs/60:.1f} min")
    return {"mp4": str(mp4), "frames_dir": str(fd), "frames": min(len(imgs), shot["frames"]), "render_s": round(secs, 1), "prompt_id": pid}
