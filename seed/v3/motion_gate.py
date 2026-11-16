#!/usr/bin/env python3
"""v3 motion gate (numpy + OpenCV only, runs on the box qcvenv and on the PC mtvenv).
Why: the v2 qc_gate local_motion counts light sweeps, rain and grain as motion, so shots the strict review called
"animated stills / slow zooms" (1-04n lm 1.06, 2-08bn 0.98, 4-02n 1.02) passed. This gate measures what a viewer sees:
  subject   p90 residual optical flow after removing the global camera move, on per-frame contrast-normalised
            images (light sweeps / exposure pulses cancel out), median over the shot       [px/frame @180x320]
  change    how different the last frame is from the first after aligning the camera (pose/position change)
  travel    range of the main face's centre and size over the shot (fraction of frame height; 0 if no face)
  camera    total camera translation (px @180x320) + |log scale| (dolly/zoom)
  -> motion score = subject*10 + change*20 + travel*20 (+ camera term only when subject also moves: a pure zoom is Ken Burns)
usage: motion_gate.py VIDEO [--kind character|insert] [--min SCORE] [--json OUT]   exit 0 PASS / 1 FAIL"""
import argparse, json, sys, math
import numpy as np, cv2

def frames(path, fps=8, size=(180, 320)):
    cap = cv2.VideoCapture(path); src = cap.get(cv2.CAP_PROP_FPS) or 16; step = max(1, int(round(src / fps))); out = []; i = 0
    while True:
        ok, f = cap.read()
        if not ok: break
        if i % step == 0: out.append(cv2.resize(f, size, interpolation=cv2.INTER_AREA))
        i += 1
    return out, src / step

def norm(g):
    g = cv2.GaussianBlur(g.astype(np.float32), (5, 5), 0); m, s = g.mean(), g.std() + 1e-3
    return np.clip((g - m) / s * 40 + 128, 0, 255).astype(np.uint8)

def affine_from_flow(flow):
    h, w = flow.shape[:2]; ys, xs = np.mgrid[4:h:8, 4:w:8]; p0 = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
    p1 = p0 + flow[ys.ravel(), xs.ravel()]
    M, _ = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=1.0)
    return M if M is not None else np.array([[1, 0, 0], [0, 1, 0]], np.float32)

def run(path, kind="character"):
    fr, fps = frames(path); g = [norm(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)) for f in fr]
    if len(g) < 4: return {"error": "too short"}
    h, w = g[0].shape; ys, xs = np.mgrid[0:h, 0:w].astype(np.float32); subj = []; area = []; cam_t = 0.0; cam_s = 0.0; Macc = np.eye(3)
    for a, b in zip(g[:-1], g[1:]):
        fl = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0); M = affine_from_flow(fl)
        gx = M[0, 0] * xs + M[0, 1] * ys + M[0, 2] - xs; gy = M[1, 0] * xs + M[1, 1] * ys + M[1, 2] - ys
        res = np.hypot(fl[..., 0] - gx, fl[..., 1] - gy); subj.append(float(np.percentile(res, 90))); area.append(float((res > 1.0).mean()))
        cam_t += float(np.hypot(M[0, 2], M[1, 2])); cam_s += float(math.log(max(1e-3, math.hypot(M[0, 0], M[1, 0]))))
        Macc = np.vstack([M, [0, 0, 1]]) @ Macc
    # first vs last after aligning the accumulated camera move
    warped = cv2.warpAffine(g[0], Macc[:2].astype(np.float32), (w, h), borderMode=cv2.BORDER_REFLECT)
    m = np.zeros((h, w), np.uint8); m[16:-16, 16:-16] = 1
    change = float(np.mean(np.abs(warped.astype(np.float32) - g[-1].astype(np.float32))[m > 0]) / 255.0)
    # face travel (Haar frontal + profile), on the raw frames
    cas = [cv2.CascadeClassifier(cv2.data.haarcascades + n) for n in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")]
    cs = []
    for f in fr:
        gg = cv2.equalizeHist(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)); best = None
        for c in cas:
            for (x, y, ww, hh) in c.detectMultiScale(gg, 1.1, 4, minSize=(24, 24)):
                if best is None or ww * hh > best[2] * best[3]: best = (x, y, ww, hh)
        if best is not None: cs.append((best[0] + best[2] / 2, best[1] + best[3] / 2, best[2]))
    travel = 0.0
    if len(cs) >= max(3, len(fr) // 4):
        a = np.array(cs); travel = float(max(np.ptp(a[:, 0]), np.ptp(a[:, 1])) / h + abs(math.log(a[:, 2].max() / a[:, 2].min())) / 3)
    s = float(np.median(subj)); s90 = float(np.percentile(subj, 90))
    score = s * 10 + change * 20 + travel * 20
    if s > 0.6: score += min(3.0, cam_t / 40 + abs(cam_s) * 10)
    return {"video": path, "kind": kind, "n": len(g), "subject_med": round(s, 3), "subject_p90": round(s90, 3), "change": round(change, 3),
            "travel": round(travel, 3), "faces_found": len(cs), "camera_px": round(cam_t, 1), "camera_logscale": round(cam_s, 3), "area_med": round(float(np.median(area)), 3), "area_p75": round(float(np.percentile(area, 75)), 3), "score": round(score, 2)}

# Calibrated 2026-11-14 00:35 on the 16 takes the strict review flagged vs 8 it accepted (qc/v3gate/motion_cal.json):
# every flagged character shot has change <= 0.061 (start pose == end pose) and area_med <= 0.19; flagged inserts
# (arrow blink 1-08, necklace 4-04n, phone 4-11, door 5-01) have change <= 0.023 with area <= 0.106.
RULES = {"character": {"change": 0.10, "area_med": 0.20, "subject_med": 1.5},
         "insert": {"area_med": 0.11, "change_or_camera": (0.012, 10.0)}}
def verdict_for(r, kind):
    q = RULES.get(kind, RULES["insert"]); bad = []
    if "error" in r: return {"verdict": "FAIL", "reasons": [r["error"]]}
    for k in ("change", "area_med", "subject_med"):
        if k in q and r[k] < q[k]: bad.append(f"{k} {r[k]} < {q[k]}")
    if "change_or_camera" in q and not (r["change"] >= q["change_or_camera"][0] or r["camera_px"] >= q["change_or_camera"][1]):
        bad.append(f"change {r['change']} < {q['change_or_camera'][0]} and camera {r['camera_px']} < {q['change_or_camera'][1]}")
    return {"verdict": "FAIL" if bad else "PASS", "reasons": [("STILL_v3 " + kind + ": " + "; ".join(bad))] if bad else [], "rules": q}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("video"); ap.add_argument("--kind", default="character"); ap.add_argument("--min", type=float); ap.add_argument("--json")
    a = ap.parse_args(); r = run(a.video, a.kind)
    r.update(verdict_for(r, a.kind))
    if a.json: open(a.json, "w").write(json.dumps(r, indent=1))
    print(json.dumps(r)); sys.exit(0 if r["verdict"] == "PASS" else 1)
