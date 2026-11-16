"""Stage 2b: repair MuseTalk output frame by frame (CPU, numpy/OpenCV only).

Found in the 4-06 test: MuseTalk's 256 px face patch (a) softens the whole lower face
(freckles/skin texture gone, lips turn pink) and (b) on some frames pastes a smeared
rectangle (blend failure, e.g. frame 18). This stage:
 1. keeps MuseTalk's pixels only inside a feathered LOWER-FACE mask built from the tracked
    68 landmarks (nose base -> jaw line), everything else comes from the source frame;
 2. re-injects the source frame's fine detail (high-pass, sigma ~2 px) inside that mask so
    skin texture/grain match the rest of the shot;
 3. flags frames whose lower face differs abnormally from the source outside the mouth
    (median + k*MAD) and rebuilds them from the neighbouring good frames ("regenerate only
    the broken frames"). Writes postfix_report.json.
"""
import json, sys
from pathlib import Path
import cv2
import numpy as np

def lower_face_mask(lm, shape, grow=1.12, feather=0.06):
    lm = np.asarray(lm, np.float32)
    pts = np.concatenate([lm[2:15], lm[[35, 34, 33, 32, 31]]], 0)   # jaw 2..14 + nose base
    c = pts.mean(0); pts = c + (pts - c) * grow
    m = np.zeros(shape[:2], np.float32)
    cv2.fillPoly(m, [pts.astype(np.int32)], 1.0)
    k = int(max(3, feather * (lm[:, 0].max() - lm[:, 0].min()))) | 1
    return cv2.GaussianBlur(m, (k * 2 + 1, k * 2 + 1), 0)

def mouth_mask(lm, shape, grow=1.5):
    lm = np.asarray(lm, np.float32); pts = lm[48:60]
    c = pts.mean(0); pts = c + (pts - c) * grow
    m = np.zeros(shape[:2], np.float32); cv2.fillPoly(m, [pts.astype(np.int32)], 1.0)
    return cv2.GaussianBlur(m, (9, 9), 0)

def highpass(img, s=2.0):
    f = img.astype(np.float32)
    return f - cv2.GaussianBlur(f, (0, 0), s)

def run(src_dir, lip_dir, track_json, out_dir, k_mad=5.0, detail=0.85):
    src = sorted(Path(src_dir).glob("*.png")); lip = sorted(Path(lip_dir).glob("*.png"))
    tr = json.load(open(track_json)); lms = tr["landmarks68"]
    n = min(len(src), len(lip)); out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    last = next(l for l in lms if l is not None)
    frames, scores = [], []
    for i in range(n):
        s = cv2.imread(str(src[i])).astype(np.float32); o = cv2.imread(str(lip[i])).astype(np.float32)
        lm = lms[i] if i < len(lms) and lms[i] is not None else last; last = lm
        M = lower_face_mask(lm, s.shape)[..., None]; MM = mouth_mask(lm, s.shape)[..., None]
        # detail transfer: low frequencies from MuseTalk, high frequencies from source
        o_det = cv2.GaussianBlur(o, (0, 0), 2.0) + detail * highpass(s) + (1 - detail) * highpass(o)
        # inside the lips keep MuseTalk's own detail (teeth/inner mouth are new content)
        o_fix = o_det * (1 - MM) + o * MM
        f = s * (1 - M) + o_fix * M
        ring = (M[..., 0] > 0.5) & (MM[..., 0] < 0.05)
        d = np.abs(cv2.GaussianBlur(o, (0, 0), 3) - cv2.GaussianBlur(s, (0, 0), 3)).mean(2)[ring]
        scores.append(float(np.percentile(d, 95)) if d.size else 0.0)
        frames.append(f)
    sc = np.array(scores); med = np.median(sc); mad = np.median(np.abs(sc - med)) + 1e-6
    bad = [i for i in range(n) if sc[i] > med + k_mad * 1.4826 * mad]
    good = [i for i in range(n) if i not in bad]
    for i in bad:
        lo = max([g for g in good if g < i], default=None); hi = min([g for g in good if g > i], default=None)
        if lo is None or hi is None:
            j = lo if hi is None else hi; frames[i] = frames[j].copy()
        else:
            w = (i - lo) / (hi - lo)
            s = cv2.imread(str(src[i])).astype(np.float32)
            lm = lms[i] or last; M = lower_face_mask(lm, s.shape)[..., None]
            blend = frames[lo] * (1 - w) + frames[hi] * w
            frames[i] = s * (1 - M) + blend * M
    for i, f in enumerate(frames):
        cv2.imwrite(str(out_dir / f"{i:05d}.png"), np.clip(f, 0, 255).astype(np.uint8))
    rep = {"frames": n, "median_ring_p95": round(float(med), 2), "mad": round(float(mad), 2),
           "broken_frames_rebuilt": bad, "scores": [round(x, 2) for x in scores]}
    json.dump(rep, open(out_dir.parent / "postfix_report.json", "w"), indent=1)
    print("postfix: rebuilt frames", bad)
    return rep

if __name__ == "__main__":
    run(*sys.argv[1:5])
