#!/usr/bin/env python3
"""SatrCut v2 assembly: the episode is built ONLY from a frozen manifest of passed takes (sha256-checked), so a stale,
deleted or swapped shot can never get into the cut (ep1: stale default shot file, deleted conforms, the 19:01
'final' containing shots that had been replaced). Then the finished file is checked for mouths moving with no
dialogue and for lines with no picture/audio.

  python3 assemble_v2.py freeze --shots SHOTS.json --out EPISODE_manifest.json [--allow-human-accept]
  python3 assemble_v2.py build  --manifest EPISODE_manifest.json            # verifies, then satrcut.py assemble
  python3 assemble_v2.py check  --manifest EPISODE_manifest.json --video FINAL.mp4 [--out report.json]
     (check needs v2/qcvenv/bin/python for the mouth scan; the static checks run anywhere)
"""
import argparse, hashlib, json, os, subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; OWN = HERE.parent; PROJECT = OWN.parent.parent
LINE_NOTE = ("line", "o.s.", "v.o.", "voicemail", "pre-lap", "prelap")

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()

def dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout or 0)

NAMES = ("adam", "claire", "rachel", "mara", "noor", "sunny", "eli", "jonah", "dana")
def line_speaker(x, s):
    import re
    t = (x.get("note", "") + " " + Path(x.get("file", "")).stem).lower()
    m = re.search(r"\b(" + "|".join(NAMES) + r")\b[^a-z]*(o\.s\.|v\.o\.|voicemail)", t)
    if m: return m.group(1) + (" (v.o.)" if m.group(2) != "o.s." else "")
    for n in NAMES:
        if re.search(r"_" + n + r"(_|\b)", t): return n
    return (s.get("speaker") or "").split(" (")[0].strip().lower()

def lines_of(s):
    out = []
    for x in s.get("sfx", []):
        if any(k in (x.get("note", "") + " " + x.get("kind", "")).lower() for k in LINE_NOTE) or "/lines" in x.get("file", "") or "dialogue" in x.get("file", ""):
            out.append({"file": x["file"], "at_s": x.get("at_s", 0.0), "speaker": line_speaker(x, s), "note": x.get("note", "")[:80]})
    if s.get("audio"): out.append({"file": s["audio"], "at_s": s.get("audio_offset_s", 0.2), "on_take": True})
    return out

def resolve(f):
    for b in (Path(f), PROJECT / f, OWN / f, PROJECT / "production" / f):
        if b.exists(): return b
    return None

def take_audio_line(f):
    """lip-sync / reuse takes carry the line inside the take: extract it if it is not silent"""
    out = HERE / "cache" / "take_audio" / (Path(f).stem + ".wav"); out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(f), "-vn", "-ac", "1", "-ar", "16000", str(out)], capture_output=True)
        if r.returncode or not out.exists(): return None
    vol = subprocess.run(["ffmpeg", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    mx = [l for l in vol.splitlines() if "max_volume" in l]
    return str(out) if mx and float(mx[0].split(":")[1].split()[0]) > -30 else None

def cmd_freeze(a):
    shots_p = Path(a.shots).resolve(); doc = json.loads(shots_p.read_text()); rows, problems, t = [], [], 0.0
    for s in doc["shots"]:
        stp = OWN / "state" / f"{s['id']}.json"
        if not stp.exists(): problems.append(f"{s['id']}: no state"); continue
        S = json.loads(stp.read_text()); tk = S.get("accepted")
        if not tk: problems.append(f"{s['id']}: no accepted take"); continue
        T = S["takes"][tk]; f = Path(T.get("final", ""))
        if not f.exists(): problems.append(f"{s['id']}: accepted file missing {f}"); continue
        g = (T.get("gate") or {}).get("verdict")
        human = str(T.get("review_status", "")).upper().startswith("ACCEPT")
        if g not in ("PASS", "TRIM") and not (a.allow_human_accept and human):
            problems.append(f"{s['id']}: take {tk} has no gate PASS/TRIM (gate={g}, review={T.get('review_status', '')[:40]})"); continue
        L = lines_of(s)
        if s.get("line") and not L:
            ta = take_audio_line(f)
            if ta: L = [{"file": ta, "at_s": 0.0, "on_take": True}]
        d = dur(f); fv = s.get("faces_visible", s.get("in_frame", []))
        rows.append({"id": s["id"], "take": tk, "file": str(f), "sha256": sha(f), "duration_s": round(d, 4), "start_s": round(t, 4),
                     "gate": g, "human_review": T.get("review_status", "")[:200], "scene": s.get("scene"), "faces_visible": fv, "mouth_hidden": [n.split(" (")[0] for n in s.get("mouth_hidden", [])],
                     "speaker": s.get("speaker", ""), "line_mode": s.get("line_mode"), "subtitle": s.get("subtitle", ""),
                     "subtitle_at_s": s.get("subtitle_at_s"), "subtitle_end_s": s.get("subtitle_end_s"), "line": s.get("line", ""), "lines": L})
        t += d
    m = {"shots_file": str(shots_p), "shots_sha256": sha(shots_p), "frozen": time.strftime("%F %T"), "runtime_s": round(t, 3), "shots": rows, "problems": problems}
    Path(a.out).write_text(json.dumps(m, indent=1)); print(f"froze {len(rows)} shots, {t:.1f} s -> {a.out}")
    for p in problems: print("PROBLEM", p)
    sys.exit(1 if problems else 0)

def verify(m):
    errs = []
    if sha(m["shots_file"]) != m["shots_sha256"]: errs.append(f"shot list changed since freeze: {m['shots_file']}")
    for r in m["shots"]:
        S = json.loads((OWN / "state" / f"{r['id']}.json").read_text())
        if S.get("accepted") != r["take"]: errs.append(f"{r['id']}: state now points at {S.get('accepted')}, manifest froze {r['take']}")
        if not Path(r["file"]).exists(): errs.append(f"{r['id']}: file missing {r['file']}")
        elif sha(r["file"]) != r["sha256"]: errs.append(f"{r['id']}: file changed since freeze (sha mismatch) {r['file']}")
    ids = [s["id"] for s in json.loads(Path(m["shots_file"]).read_text())["shots"]]
    if ids != [r["id"] for r in m["shots"]]: errs.append("shot order/ids in the shot list differ from the manifest")
    return errs

def cmd_build(a):
    m = json.loads(Path(a.manifest).read_text())
    if m.get("problems"): sys.exit(f"manifest has problems, refreeze: {m['problems']}")
    errs = verify(m)
    if errs:
        for e in errs: print("REFUSED", e)
        sys.exit(2)
    env = dict(os.environ, SATRCUT_SHOTS=m["shots_file"])   # never the stale default (ep01_own_tool_shots.json)
    subprocess.run([sys.executable, str(OWN / "satrcut.py"), "assemble"], check=True, env=env)
    out = sorted((OWN / "episode").glob("*_FULL_*.mp4"), key=lambda p: p.stat().st_mtime)[-1]
    m["built"] = {"file": str(out), "sha256": sha(out), "at": time.strftime("%F %T")}; Path(a.manifest).write_text(json.dumps(m, indent=1))
    print("built", out)

def static_checks(m):
    issues = []; seen_faces = {}; prev = None; warned = set()
    for r in m["shots"]:
        vo = "(v.o.)" in (r.get("speaker") or "").lower() or r.get("line_mode") == "vo"
        sc = r.get("scene"); spk = (r.get("speaker") or "").split(" (")[0].strip().lower()
        vis = [n.split(" (")[0].lower() for n in r.get("faces_visible", [])]
        overhang = (r.get("subtitle_at_s") or 0) < 0 and prev and prev["lines"]   # subtitle pre-laps from the previous shot's line
        if (r.get("subtitle") or r.get("line")) and not r["lines"] and not overhang:
            issues.append(("ERROR", r["id"], "SUBTITLE_NO_AUDIO", f"subtitle '{r['subtitle'][:40]}' but no line audio on this shot"))
        for ln in r["lines"]:
            if not resolve(ln["file"]):
                issues.append(("ERROR", r["id"], "LINE_FILE_MISSING", ln["file"]))
        for ln in r["lines"]:
            if "exhale" in ln.get("note", "").lower() or "breath" in ln.get("note", "").lower(): continue
            sp = (ln.get("speaker") or spk or "").split(" (")[0]; lvo = vo or "(v.o.)" in (ln.get("speaker") or "")
            if not sp or ln.get("on_take") or lvo: continue
            pv = prev and prev.get("scene") == sc and sp in [n.split(" (")[0].lower() for n in prev.get("faces_visible", [])]
            hidden = [n.lower() for n in r.get("mouth_hidden", [])]
            if sp in vis and sp not in hidden and r.get("line_mode") != "lipsync_cu":
                issues.append(("ERROR", r["id"], "LINE_OVER_SPEAKERS_FACE", f"{sp}'s line plays while {sp} is in frame without lip-sync ({ln.get('note', '')[:40]}); make the speaker's mouth invisible (back/over-shoulder) and set faces_visible, or move the line"))
            elif sp not in vis and vis and not pv and (sc, sp) not in warned:
                warned.add((sc, sp))
                issues.append(("WARN", r["id"], "UNATTRIBUTED_OS_LINE", f"{sp}'s O.S. line starts cold on {vis}'s face; viewers read it as the visible person's line (4-06b review). Start it on {sp}'s shot or a faceless insert, or cut straight from {sp} to the listener"))
        if False and spk and r["lines"] and r.get("line_mode") != "lipsync_cu" and spk in vis:
            issues.append(("ERROR", r["id"], "LINE_OVER_SPEAKERS_CLOSED_MOUTH", f"{spk}'s line plays while {spk}'s face is in frame without lip-sync"))
        if False and spk and r["lines"] and spk not in vis and vis and not (prev and prev.get("scene") == sc and spk in [n.split(" (")[0].lower() for n in prev.get("faces_visible", [])]) and not vo:
            issues.append(("WARN", r["id"], "UNATTRIBUTED_OS_LINE", f"{spk}'s O.S. line starts cold on {vis}'s face; viewers read it as the visible person's line (4-06b review). Start it on the speaker's own shot or a faceless insert, or cut from the speaker straight to the listener"))
        seen_faces.setdefault(sc, set()).update(vis); prev = r
    return issues

def speech_mask(m, fps=8.0):
    import soundfile as sf, numpy as np
    T = m["runtime_s"] + 2; n = int(T * fps) + 1; mask = np.zeros(n, bool)
    for r in m["shots"]:
        for ln in r["lines"]:
            f = resolve(ln["file"])
            if not f: continue
            y, sr = sf.read(str(f)); y = y.mean(1) if y.ndim > 1 else y
            hop = int(sr / fps); env = np.array([np.sqrt(np.mean(y[i:i + hop] ** 2)) for i in range(0, len(y) - hop, hop)])
            if not len(env): continue
            on = env > max(0.02, 0.15 * env.max()); t0 = r["start_s"] + ln["at_s"]
            for k, v in enumerate(on):
                i = int(round(t0 * fps)) + k
                if v and 0 <= i < n: mask[max(0, i - 2): i + 3] = True   # +-0.25 s slack
    return mask

def cmd_check(a):
    m = json.loads(Path(a.manifest).read_text()); issues = static_checks(m)
    if a.video:
        import cv2, numpy as np, mediapipe as mp, math
        fps_s = 8.0; mask = speech_mask(m, fps_s); cap = cv2.VideoCapture(a.video); vf = cap.get(cv2.CAP_PROP_FPS) or 24; step = max(1, round(vf / fps_s))
        mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=2, min_detection_confidence=0.4); ser = []; i = 0; cuts = []; ph = None
        while True:
            ok, fr = cap.read()
            if not ok: break
            if i % step == 0:
                hsv = cv2.cvtColor(cv2.resize(fr, (90, 160)), cv2.COLOR_BGR2HSV); hh = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256]); cv2.normalize(hh, hh)
                if ph is not None and cv2.compareHist(ph, hh, cv2.HISTCMP_CORREL) < 0.6: cuts.append(i / vf - 0.06)
                ph = hh
                H, W = fr.shape[:2]; res = mesh.process(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)); v = None
                if res.multi_face_landmarks:
                    L = res.multi_face_landmarks[0].landmark
                    v = math.dist((L[13].x * W, L[13].y * H), (L[14].x * W, L[14].y * H)) / (math.dist((L[61].x * W, L[61].y * H), (L[291].x * W, L[291].y * H)) + 1e-6)
                ser.append((i / vf, v))
            i += 1
        mesh.close()
        # mouth moving with no dialogue: 1 s windows where the mouth range is talking-sized and no line is playing
        vd = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or i) / vf
        bounds = [r["start_s"] for r in m["shots"]] + [vd] + cuts   # manifest cut points + cuts measured in the picture
        shot_at = lambda t: next((r["id"] for r, b0, b1 in zip(m["shots"], bounds, bounds[1:]) if b0 <= t < b1), "?")
        if a.dump: Path(a.dump).write_text(json.dumps(ser))
        win = int(fps_s); flagged = []
        for k in range(0, len(ser) - win, win // 2):
            seg = [v for _, v in ser[k:k + win] if v is not None]
            if len(seg) < win * 0.7: continue
            ta, tb = ser[k][0], ser[min(k + win, len(ser) - 1)][0]
            if any(ta + 0.05 < b < tb - 0.05 for b in bounds): continue   # never measure across a cut
            rng = float(np.percentile(seg, 95) - np.percentile(seg, 5)); idx = int(ser[k][0] * fps_s)
            talking = mask[idx: idx + win].any() if idx < len(mask) else False
            if rng > a.mouth_range and not talking:
                t0 = ser[k][0]; shot = shot_at(t0)
                if not flagged or t0 - flagged[-1][0] > 0.6: flagged.append((round(t0, 2), shot, round(rng, 3)))
        # lips held open with no line (3-04c review): >=0.75 s of open mouth while silent
        run = []
        for t, v in ser + [(1e9, None)]:
            idx = int(t * fps_s); silent = not (idx < len(mask) and mask[idx])
            if v is not None and v > a.mouth_open and silent: run.append(t); continue
            if len(run) >= a.open_min_s * fps_s:
                shot = shot_at(run[0])
                issues.append(("WARN", shot, "MOUTH_OPEN_NO_AUDIO", f"{run[0]:.2f}-{run[-1]:.2f} s: lips held open with no line playing"))
            run = []
        for t0, shot, rng in flagged:
            issues.append(("ERROR", shot, "MOUTH_MOVES_NO_AUDIO", f"{t0}-{t0 + 1:.2f} s: mouth range {rng} with no line playing"))
    for lvl, sid, rule, msg in issues: print(f"{lvl:5} {sid:8} {rule:32} {msg}")
    ne = sum(x[0] == "ERROR" for x in issues); print(f"\n{ne} errors, {len(issues) - ne} warnings")
    if a.out: Path(a.out).write_text(json.dumps(issues, indent=1))
    sys.exit(1 if ne else 0)

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="c", required=True)
    p = sp.add_parser("freeze"); p.add_argument("--shots", required=True); p.add_argument("--out", required=True); p.add_argument("--allow-human-accept", action="store_true")
    p = sp.add_parser("build"); p.add_argument("--manifest", required=True)
    p = sp.add_parser("check"); p.add_argument("--manifest", required=True); p.add_argument("--video"); p.add_argument("--out"); p.add_argument("--mouth-range", type=float, default=0.12); p.add_argument("--mouth-open", type=float, default=0.09); p.add_argument("--dump"); p.add_argument("--open-min-s", type=float, default=0.75)
    a = ap.parse_args(); globals()["cmd_" + a.c](a)

if __name__ == "__main__":
    main()
