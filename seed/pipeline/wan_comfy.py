"""Stage 1: image-to-video with Wan2.2-TI2V-5B (Apache-2.0) through headless ComfyUI.

Same ComfyUI commit and the same three weight files (sha256-pinned) that completed
Kaggle attempt 4 on a T4 (46:54 for 81 frames x 50 steps at 704x1280).
"""
import json, os, shutil, subprocess, sys, time, urllib.request, uuid
from pathlib import Path
from common import log, sh, sha256, hf_download

COMFY_COMMIT = "2c75dc1b379a59e0880619d029607af5169a62ef"  # v0.44.0
WAN_REPO, WAN_REV = "Comfy-Org/Wan-2.2", "ee6f4a40737a995bf5818954cfce6d59443b0f04"
WEIGHTS = [
    ("diffusion_models", "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", 9999658848,
     "456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e"),
    ("text_encoders", "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", 6735906897,
     "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68"),
    ("vae", "split_files/vae/wan2.2_vae.safetensors", 1409400960,
     "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156"),
]

def setup(root: Path, verify_sha=False):
    comfy = root / "ComfyUI"
    if not (comfy / "main.py").exists():
        sh(["git", "clone", "-q", "https://github.com/comfyanonymous/ComfyUI.git", str(comfy)])
        sh(["git", "-C", str(comfy), "checkout", "-q", COMFY_COMMIT])
    req = (comfy / "requirements.txt").read_text().splitlines()
    keep = [l for l in req if l.strip() and not l.startswith("#")
            and l.split("=")[0].split(">")[0].split("<")[0].strip() not in ("torch", "torchvision", "torchaudio")]
    (root / "comfy_req.txt").write_text("\n".join(keep) + "\n")
    sh([sys.executable, "-m", "pip", "install", "-q", "-r", str(root / "comfy_req.txt")])
    for sub, remote, size, sha in WEIGHTS:
        hf_download(WAN_REPO, WAN_REV, remote, comfy / "models" / sub / Path(remote).name,
                    expect_sha=sha if verify_sha else None, expect_size=size)
    return comfy

def start_server(comfy: Path, gpu: int, port: int, logfile: Path, extra=()):
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu))
    f = open(logfile, "w")
    p = subprocess.Popen([sys.executable, "main.py", "--listen", "127.0.0.1", "--port", str(port),
                          "--disable-auto-launch", *extra], cwd=comfy, env=env, stdout=f, stderr=subprocess.STDOUT)
    for _ in range(300):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=2)
            log("ComfyUI up on", port, "gpu", gpu)
            return p
        except Exception:
            if p.poll() is not None:
                raise RuntimeError("ComfyUI exited; see " + str(logfile))
            time.sleep(2)
    raise RuntimeError("ComfyUI did not start")

def workflow(shot, image_name, seed, prefix):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": shot["shift"]}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": shot["prompt"]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": shot["negative"]}},
        "7": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "8": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["4", 0], "width": shot["width"],
              "height": shot["height"], "length": shot["frames"], "batch_size": 1, "start_image": ["7", 0]}},
        "9": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["5", 0], "negative": ["6", 0],
              "latent_image": ["8", 0], "seed": seed, "steps": shot["steps"], "cfg": shot["cfg"],
              "sampler_name": shot["sampler"], "scheduler": shot["scheduler"], "denoise": 1.0}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["4", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": prefix}},
    }

def render(comfy: Path, port: int, shot, seed, out_dir: Path, start_png: Path, timeout_s=4 * 3600):
    out_dir.mkdir(parents=True, exist_ok=True)
    image_name = f"start_{shot['id']}.png"
    shutil.copy(start_png, comfy / "input" / image_name)
    prefix = f"satrcut_{shot['id']}_s{seed}"
    wf = workflow(shot, image_name, seed, prefix)
    (out_dir / "workflow.json").write_text(json.dumps(wf, indent=1, ensure_ascii=False))
    body = json.dumps({"prompt": wf, "client_id": uuid.uuid4().hex}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/prompt", data=body, headers={"Content-Type": "application/json"})
    pid = json.loads(urllib.request.urlopen(req).read())["prompt_id"]
    t0 = time.time()
    log("queued", pid)
    while True:
        time.sleep(15)
        h = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/history/{pid}").read())
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("completed") or st.get("status_str") in ("success", "error"):
                break
        if time.time() - t0 > timeout_s:
            raise RuntimeError("Wan render timed out")
    secs = time.time() - t0
    (out_dir / "comfy-history.json").write_text(json.dumps(h, indent=1, ensure_ascii=False))
    if h[pid]["status"].get("status_str") != "success":
        raise RuntimeError("Wan render failed: " + json.dumps(h[pid]["status"])[:2000])
    imgs = h[pid]["outputs"]["11"]["images"]
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    for i, im in enumerate(sorted(imgs, key=lambda x: x["filename"])):
        shutil.move(comfy / "output" / im.get("subfolder", "") / im["filename"], frames_dir / f"{i:05d}.png")
    mp4 = out_dir / f"wan_{shot['id']}_s{seed}_{shot['width']}x{shot['height']}_{shot['fps']}fps.mp4"
    sh(["ffmpeg", "-y", "-v", "error", "-framerate", str(shot["fps"]), "-i", str(frames_dir / "%05d.png"),
        "-c:v", "libx264", "-preset", "slow", "-crf", "12", "-pix_fmt", "yuv420p", str(mp4)])
    log(f"Wan {shot['id']} seed {seed}: {len(imgs)} frames in {secs/60:.1f} min")
    return {"mp4": str(mp4), "frames_dir": str(frames_dir), "frames": len(imgs), "render_s": round(secs, 1), "prompt_id": pid}
