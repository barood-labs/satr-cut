"""Keyframe operations shared by keyframes.py (box) and runner_v2.py (PC). Pillow only."""
import hashlib
from pathlib import Path
from PIL import Image, ImageOps

def sha256(p):
    h = hashlib.sha256(); h.update(Path(p).read_bytes()); return h.hexdigest()

def fit(src, dst, size=(720, 1280)):
    ImageOps.fit(Image.open(src).convert("RGB"), size, Image.LANCZOS).save(dst); return dst

def crop_tighter(src, dst, bottom=0.12, size=(720, 1280)):
    """Auto-remedy for HAND fails: drop the bottom of the frame (where hands enter) and keep 9:16 by trimming the
    sides equally, then refit. 2-04b/2-08b/3-04b2/4-02 only passed after collar-up crops (fix4, fix7)."""
    im = Image.open(src).convert("RGB"); W, H = im.size; nh = int(H * (1 - bottom)); nw = int(nh * size[0] / size[1])
    x0 = max(0, (W - nw) // 2); im.crop((x0, 0, x0 + min(nw, W), nh)).resize(size, Image.LANCZOS).save(dst); return dst

def drift_end(src, dst, scale=1.04, dx=0.0, dy=-0.01, size=(720, 1280)):
    """End keyframe for anchor 'drift': the start frame re-framed ~4% tighter (a planned slow push-in). Gives the model
    a reason to move instead of freezing (pinned takes went near-still: 3-04b2 s101, 5-01 s303) while still
    pinning anatomy, wardrobe and the closed door."""
    im = Image.open(src).convert("RGB").resize(size, Image.LANCZOS); W, H = size
    cw, ch = int(W / scale), int(H / scale); cx, cy = W / 2 + dx * W, H / 2 + dy * H
    x0 = int(min(max(0, cx - cw / 2), W - cw)); y0 = int(min(max(0, cy - ch / 2), H - ch))
    im.crop((x0, y0, x0 + cw, y0 + ch)).resize(size, Image.LANCZOS).save(dst); return dst
