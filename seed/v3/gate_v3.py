#!/usr/bin/env python3
"""v3 take gate = motion_gate (stillness) + first/last-frame sanity (numpy + OpenCV only; box qcvenv / PC mtvenv).
Why the extra checks: v3 renders use WanFirstLastFrameToVideo with start and end keyframes that differ a lot, so
'change' is high by construction; a render that just DISSOLVES start->end, smears (blur), fills with noise from the
no-LoRA high expert, or hard-cuts would still pass motion_gate. Extra FAIL reasons:
  DISSOLVE   mid frames are a linear blend of first+last (R2 >= 0.975, +0.08 over either endpoint alone, both weights >= 0.2, at 2 of 3 probes)
  SOFT       mid-frame sharpness < 0.35 x the first frame's (Laplacian variance)
  NOISY      mid-frame high-frequency energy > 3.0 x the first frame's
  CUT        one frame-to-frame jump > 6 x the median jump and > 18/255 (scene change / flash)
  kind=kiss (v5, Nov 14 12:11 kiss rule): motion via insert rules + MERGE/MORPH check in the central (lips/faces) region:
    MORPH      some frame matches NEITHER keyframe (max NCC to first/last < 0.55) -> faces re-formed mid-shot
    SMEAR      central-region sharpness of any mid frame < 0.5 x the keyframes' (fused/melted faces)
    always sets review=true (human look before use; runner retakes up to the shot's presets list)
usage: gate_v3.py VIDEO [--kind character|insert|kiss] [--json OUT]   exit 0 PASS / 1 FAIL"""
import argparse, json, sys
import numpy as np, cv2
sys.path.insert(0, __import__("os").path.dirname(__file__))
import motion_gate as mg

def gray_frames(path, size=(180, 320)):
    cap = cv2.VideoCapture(path); out = []
    while True:
        ok, f = cap.read()
        if not ok: break
        out.append(cv2.cvtColor(cv2.resize(f, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32))
    return out

def lapvar(g): return float(cv2.Laplacian(g, cv2.CV_32F).var())
def hf(g): return float(np.mean(np.abs(g - cv2.GaussianBlur(g, (0, 0), 1.0))))

def sanity(path):
    g = gray_frames(path)
    if len(g) < 8: return {"error": "too short"}
    f0, fn = g[0], g[-1]; n = len(g); probes = []
    X = np.stack([f0.ravel(), fn.ravel(), np.ones(f0.size, np.float32)], 1)
    for q in (0.3, 0.5, 0.7):
        m = g[int(round(q * (n - 1)))].ravel()
        coef, *_ = np.linalg.lstsq(X, m, rcond=None); pred = X @ coef
        tot = (((m - m.mean()) ** 2).sum() + 1e-6); r2 = 1 - float(((m - pred) ** 2).sum() / tot)
        single = []
        for k in (0, 1):   # how well ONE endpoint alone explains the mid frame (high for near-static shots)
            Xs = X[:, [k, 2]]; cs, *_ = np.linalg.lstsq(Xs, m, rcond=None); single.append(1 - float(((m - Xs @ cs) ** 2).sum() / tot))
        probes.append({"q": q, "r2": round(r2, 4), "gain": round(r2 - max(single), 4), "a": round(float(coef[0]), 3), "b": round(float(coef[1]), 3)})
    mid = g[n // 2]
    sharp = lapvar(mid) / (lapvar(f0) + 1e-6); noise = hf(mid) / (hf(f0) + 1e-6)
    jumps = np.array([np.mean(np.abs(b - a)) for a, b in zip(g[:-1], g[1:])])
    cut = float(jumps.max() / (np.median(jumps) + 1e-3))
    return {"dissolve_probes": probes, "sharp_ratio": round(sharp, 3), "noise_ratio": round(noise, 3),
            "jump_max": round(float(jumps.max()), 2), "jump_ratio": round(cut, 2)}

def sanity_reasons(s):
    if "error" in s: return [s["error"]]
    bad = []
    blended = [p for p in s["dissolve_probes"] if p["r2"] >= 0.975 and p["gain"] >= 0.08 and p["a"] >= 0.2 and p["b"] >= 0.2]
    if len(blended) >= 2: bad.append(f"DISSOLVE start->end blend at {[p['q'] for p in blended]}")
    if s["sharp_ratio"] < 0.35: bad.append(f"SOFT sharp_ratio {s['sharp_ratio']}")
    if s["noise_ratio"] > 3.0: bad.append(f"NOISY noise_ratio {s['noise_ratio']}")
    if s["jump_ratio"] > 6 and s["jump_max"] > 18: bad.append(f"CUT jump {s['jump_max']} ({s['jump_ratio']}x median)")
    return bad

def ncc(a, b):
    a = a - a.mean(); b = b - b.mean(); return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))

def merge_check(path):
    g = gray_frames(path, size=(360, 640))
    if len(g) < 8: return {"error": "too short"}, ["too short"]
    h, w = g[0].shape; crop = lambda f: f[int(.25 * h):int(.80 * h), int(.18 * w):int(.82 * w)]
    c = [crop(f) for f in g]; f0, fn = c[0], c[-1]
    match = [max(ncc(f, f0), ncc(f, fn)) for f in c]
    ref_sharp = min(lapvar(f0), lapvar(fn)) + 1e-6
    sh = [lapvar(f) / ref_sharp for f in c[2:-2]]
    m = {"match_min": round(min(match), 3), "match_at": int(np.argmin(match)), "sharp_min": round(min(sh), 3) if sh else None}
    bad = []
    if m["match_min"] < 0.55: bad.append(f"MORPH central region matches neither keyframe (NCC {m['match_min']} at frame {m['match_at']})")
    if sh and m["sharp_min"] < 0.5: bad.append(f"SMEAR central sharpness {m['sharp_min']} x keyframes (fused/melted faces)")
    return m, bad

def gate(path, kind="character"):
    mk = "insert" if kind == "kiss" else kind
    r = mg.run(path, mk); r.update(mg.verdict_for(r, mk)); r["kind"] = kind
    if kind == "kiss":
        m, bad = merge_check(path); r["merge"] = m; r["review"] = True
        if bad: r["verdict"] = "FAIL"; r["reasons"] = r.get("reasons", []) + bad
    s = sanity(path); r["sanity"] = s; extra = sanity_reasons(s)
    if extra: r["verdict"] = "FAIL"; r["reasons"] = r.get("reasons", []) + extra
    return r

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("video"); ap.add_argument("--kind", default="character"); ap.add_argument("--json")
    a = ap.parse_args(); r = gate(a.video, a.kind)
    if a.json: open(a.json, "w").write(json.dumps(r, indent=1))
    print(json.dumps(r)); sys.exit(0 if r["verdict"] == "PASS" else 1)
