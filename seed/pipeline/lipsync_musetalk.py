"""Stage 2: audio-driven lip-sync with MuseTalk 1.5 (code MIT, weights 'any purpose,
even commercially'). Runs in its own Python 3.10 venv so it never fights the
ComfyUI/torch stack of the host image. No mmcv/mmpose (see musetalk_preprocessing.py).
"""
import json, os, shutil, subprocess, sys, time
from pathlib import Path
from common import log, sh, hf_download

MUSETALK_COMMIT = "0a89dec45a0192b824e3cf4daf96c239440c5ed8"
W = [  # (repo, revision, remote path, local path under models/)
    ("TMElyralab/MuseTalk", "2bcb936e2fddb4d86db4c62fd45b387d0c061571", "musetalkV15/unet.pth", "musetalkV15/unet.pth"),
    ("TMElyralab/MuseTalk", "2bcb936e2fddb4d86db4c62fd45b387d0c061571", "musetalkV15/musetalk.json", "musetalkV15/musetalk.json"),
    ("stabilityai/sd-vae-ft-mse", "31f26fdeee1355a5c34592e401dd41e45d25a493", "config.json", "sd-vae/config.json"),
    ("stabilityai/sd-vae-ft-mse", "31f26fdeee1355a5c34592e401dd41e45d25a493", "diffusion_pytorch_model.bin", "sd-vae/diffusion_pytorch_model.bin"),
    ("openai/whisper-tiny", "169d4a4341b33bc18d8881c4b69c2e104e1cc0af", "config.json", "whisper/config.json"),
    ("openai/whisper-tiny", "169d4a4341b33bc18d8881c4b69c2e104e1cc0af", "pytorch_model.bin", "whisper/pytorch_model.bin"),
    ("openai/whisper-tiny", "169d4a4341b33bc18d8881c4b69c2e104e1cc0af", "preprocessor_config.json", "whisper/preprocessor_config.json"),
    ("ManyOtherFunctions/face-parse-bisent", "0073b233a5a3c4b1377d4dbf49245017938a72b5", "79999_iter.pth", "face-parse-bisent/79999_iter.pth"),
]
FACE_PARSE_SHA = "468e13ca13a9b43cc0881a9f99083a430e9c0a38abd935431d1c28ee94b26567"
RESNET18 = "https://download.pytorch.org/models/resnet18-5c106cde.pth"
PKGS = ["numpy<2", "diffusers==0.30.2", "transformers==4.39.2", "accelerate==0.28.0", "librosa==0.10.2",
        "einops", "omegaconf", "soundfile", "opencv-python-headless==4.9.0.80", "face-alignment==1.4.1",
        "huggingface_hub==0.30.2", "imageio", "imageio-ffmpeg", "tqdm", "scipy", "ffmpeg-python", "moviepy==1.0.3"]

def setup(root: Path, cuda=True):
    mt = root / "MuseTalk"
    if not (mt / "scripts" / "inference.py").exists():
        sh(["git", "clone", "-q", "https://github.com/TMElyralab/MuseTalk.git", str(mt)])
        sh(["git", "-C", str(mt), "checkout", "-q", MUSETALK_COMMIT])
    shutil.copy(Path(__file__).with_name("musetalk_preprocessing.py"), mt / "musetalk/utils/preprocessing.py")
    venv = root / "mtvenv"
    py = venv / "bin" / "python"
    if not py.exists():
        if shutil.which("uv") is None:
            sh([sys.executable, "-m", "pip", "install", "-q", "uv"])
        uv = shutil.which("uv") or str(Path(sys.executable).with_name("uv"))
        sh([uv, "venv", "-q", "-p", "3.10", str(venv)])
        idx = "https://download.pytorch.org/whl/cu121" if cuda else "https://download.pytorch.org/whl/cpu"
        sh([uv, "pip", "install", "-q", "-p", str(py), "--index-url", idx,
            "torch==2.4.1", "torchvision==0.19.1", "torchaudio==2.4.1"])
        sh([uv, "pip", "install", "-q", "-p", str(py)] + PKGS)
    for repo, rev, remote, local in W:
        hf_download(repo, rev, remote, mt / "models" / local,
                    expect_sha=FACE_PARSE_SHA if local.endswith("79999_iter.pth") else None)
    rn = mt / "models/face-parse-bisent/resnet18-5c106cde.pth"
    if not rn.exists():
        sh(["curl", "-L", "--fail", "-o", str(rn), RESNET18])
    return mt, py

def run(mt: Path, py: Path, shot, video: Path, audio_padded: Path, out_dir: Path, gpu=0):
    lip = shot["lipsync"]
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = out_dir / "musetalk_task.yaml"
    name = f"musetalk_{shot['id']}.mp4"
    cfg.write_text(f'task_0:\n  video_path: "{video}"\n  audio_path: "{audio_padded}"\n  result_name: "{name}"\n')
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu),
               SATRCUT_FACE_TARGET=",".join(map(str, shot["face_target_xy"])),
               SATRCUT_BBOX_SMOOTH=str(lip["bbox_smooth_window"]),
               SATRCUT_LANDMARKS_JSON=str(out_dir / "face_track.json"), PYTHONPATH=str(mt))
    cmd = [str(py), "-m", "scripts.inference", "--inference_config", str(cfg), "--result_dir", str(out_dir / "mt"),
           "--unet_model_path", "models/musetalkV15/unet.pth", "--unet_config", "models/musetalkV15/musetalk.json",
           "--whisper_dir", "models/whisper", "--version", "v15", "--fps", str(shot["fps"]),
           "--batch_size", str(lip["batch_size"]), "--extra_margin", str(lip["extra_margin"]),
           "--parsing_mode", lip["parsing_mode"], "--left_cheek_width", str(lip["left_cheek_width"]),
           "--right_cheek_width", str(lip["right_cheek_width"]), "--ffmpeg_path", "/usr/bin"]
    if lip.get("use_float16"):
        cmd.append("--use_float16")
    t0 = time.time()
    sh(cmd, env=env, cwd=mt)
    secs = time.time() - t0
    res = out_dir / "mt" / "v15" / name
    if not res.exists():
        raise RuntimeError("MuseTalk produced no output (see log above)")
    log(f"MuseTalk {shot['id']}: {secs:.0f}s")
    return {"mp4": str(res), "lipsync_s": round(secs, 1)}
