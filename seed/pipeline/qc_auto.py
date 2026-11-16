"""Automatic per-take QC (CPU). Flags, never approves: a PASS here still needs a look at the sheets.
Checks: frozen video, hard cuts (rule: one continuous shot), face lost/size jumps (morph proxy),
identity drift vs the start frame (face-crop correlation), lip-sync r (from sync_check), postfix rebuilt frames."""
import json, sys
from pathlib import Path
import numpy as np, cv2

def frames_of(video, step=1, scale=0.25):
    cap = cv2.VideoCapture(str(video)); out = []; i = 0
    while True:
        ok, f = cap.read()
        if not ok: break
        if i % step == 0: out.append(cv2.resize(f, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
        i += 1
    return out

def run(video, start_png=None, face_track=None, sync_json=None, postfix_json=None, out_json=None):
    fr = frames_of(video); g = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in fr]
    diffs = np.array([np.abs(g[i] - g[i - 1]).mean() for i in range(1, len(g))])
    med = float(np.median(diffs)); cuts = [int(i + 1) for i, d in enumerate(diffs) if d > max(12.0, 6 * med)]
    rep = {"video": str(video), "frames": len(fr), "motion_mean_diff": round(float(diffs.mean()), 3),
           "first_to_last_diff": round(float(np.abs(g[-1] - g[0]).mean()), 2), "hard_cut_frames": cuts, "flags": []}
    if rep["motion_mean_diff"] < 0.35: rep["flags"].append("FROZEN: almost no motion")
    if cuts: rep["flags"].append(f"CUT: hard change at frames {cuts}")
    if face_track and Path(face_track).exists():
        t = json.load(open(face_track)); b = np.array(t["boxes"], float)
        w = b[:, 2] - b[:, 0]; jump = np.abs(np.diff(w)) / (w[:-1] + 1e-6)
        rep["face_misses"] = t.get("misses", 0); rep["face_width_max_jump"] = round(float(jump.max()), 3)
        if t.get("misses", 0) > 0.05 * len(b): rep["flags"].append(f"FACE LOST on {t['misses']} frames")
        if jump.max() > 0.12: rep["flags"].append("FACE SIZE JUMP (possible morph)")
        if start_png:
            s = cv2.imread(str(start_png)); cap = cv2.VideoCapture(str(video)); ok, f0 = cap.read()
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); cap.set(cv2.CAP_PROP_POS_FRAMES, n - 1); ok2, fl = cap.read()
            def crop(im, bx):
                x1, y1, x2, y2 = map(int, bx); y1 = max(0, y1 - (y2 - y1) // 2)
                c = cv2.cvtColor(cv2.resize(im[y1:y2, x1:x2], (96, 96)), cv2.COLOR_BGR2GRAY).astype(np.float32)
                return (c - c.mean()) / (c.std() + 1e-6)
            if ok2:
                s = cv2.resize(s, (fl.shape[1], fl.shape[0]))
                r = float((crop(s, b[0]) * crop(fl, b[-1])).mean())
                rep["identity_corr_start_vs_last"] = round(r, 3)
                if r < 0.45: rep["flags"].append("IDENTITY DRIFT: last-frame face differs from start frame")
    if sync_json and Path(sync_json).exists():
        s = json.load(open(sync_json)); rep["lipsync_r"] = s["pearson_r"]; rep["lipsync_lag"] = s["best_lag_frames"]
        if s["pearson_r"] < 0.3: rep["flags"].append(f"LIPSYNC WEAK r={s['pearson_r']}")
    if postfix_json and Path(postfix_json).exists():
        p = json.load(open(postfix_json)); rep["postfix_rebuilt"] = p["broken_frames_rebuilt"]
        if len(p["broken_frames_rebuilt"]) > 6: rep["flags"].append("MANY BROKEN LIPSYNC FRAMES")
    rep["auto_status"] = "FLAG" if rep["flags"] else "PASS-PENDING-LOOK"
    if out_json: json.dump(rep, open(out_json, "w"), indent=1)
    print(json.dumps({k: v for k, v in rep.items()}, indent=0)[:1500]); return rep

if __name__ == "__main__":
    a = sys.argv[1:] + [None] * 6
    run(a[0], a[1], a[2], a[3], a[4], a[5])
