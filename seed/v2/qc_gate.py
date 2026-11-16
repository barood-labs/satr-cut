#!/usr/bin/env python3
"""SatrCut v2 per-take QC gate (CPU only, free tools: MediaPipe Hands/FaceMesh Apache-2.0, OpenCV YuNet MIT +
SFace Apache-2.0, Farneback optical flow). Gives a verdict a robot can act on:
    PASS           every check clean for the whole take
    REVIEW         clean, but hands are in frame by design (hand inserts): a person must look before it is used
    TRIM a-b       the longest clean window is >= the shot's need_s -> use that window, no re-render
    FAIL reasons   re-render with a new seed (runner_v2 does this automatically)

  python qc_gate.py VIDEO --spec shot.json [--start start.png] [--refs Name=sheet.jpg ...] [--out report.json]

Spec fields (all optional; the linter fills them from the shot):
  hands_allowed (false)   any detected hand / new skin blob entering the frame = defect
  faces_expected (n)      number of faces that must stay visible (0 = faceless insert)
  min_face_gap (0.45)     min gap between two face boxes, in face widths (no-contact rule)
  mouth ("closed")        closed: lips may be parted but must not move | "free" | "lipsync" (needs --audio)
  need_s (dur-0.2)        shortest usable window
  min_local_motion (0.06) residual optical flow after removing camera zoom/pan, inside the subject box
  min_photometric (1.2)   p95 of the residual brightness change after camera compensation (light flicker counts as life)
  photo_faces (0)         faces inside photo props / screens (not counted as extra people)
  identity_min (0.30)     SFace cosine to the reference sheet (same person ~0.36+ on real photos)
  wardrobe_max (0.45)     Bhattacharyya distance of the torso colour histogram vs the first frame
"""
import argparse, json, math, sys
from pathlib import Path
import numpy as np, cv2

HERE = Path(__file__).resolve().parent
YUNET = HERE / "models" / "face_detection_yunet_2023mar.onnx"
SFACE = HERE / "models" / "face_recognition_sface_2021dec.onnx"
DEF = dict(hands_allowed=False, faces_expected=None, photo_faces=0, min_face_gap=0.45, mouth="closed", need_s=None,
           min_local_motion=0.06, min_photometric=1.2, identity_min=0.30, identity_drift_min=0.30, wardrobe_max=0.45,
           min_warp0_residual=6.0, hand_tiles=False, mouth_range_max=0.15, mouth_open_max=0.30, skin_growth_max=0.012, extra_face_score=0.85, sample_fps=8)
ADVISORY = {"WARDROBE_DRIFT", "SKIN_ENTERING"}   # measured + logged, but too lighting-sensitive to fail a take (ep1 test)

def read_video(path):
    cap = cv2.VideoCapture(str(path)); fps = cap.get(cv2.CAP_PROP_FPS) or 16; fr = []
    while True:
        ok, f = cap.read()
        if not ok: break
        fr.append(f)
    return fr, fps

class Faces:
    def __init__(self):
        self.det = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.75, 0.3, 50)
        self.rec = cv2.FaceRecognizerSF.create(str(SFACE), "")
    def detect(self, img):
        h, w = img.shape[:2]; self.det.setInputSize((w, h)); _, f = self.det.detect(img)
        return [] if f is None else [r for r in f]
    def embed(self, img, row):
        return self.rec.feature(self.rec.alignCrop(img, row)).flatten()
    @staticmethod
    def cos(a, b): return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

def ref_embeddings(faces, refs):
    out = {}
    for name, p in refs.items():
        im = cv2.imread(str(p))
        if im is None: continue
        d = faces.detect(im)
        if not d: continue
        big = max(d, key=lambda r: r[2] * r[3]); out[name] = faces.embed(im, big)
    return out

def box_gap(a, b):
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]; bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    dx = max(0, max(ax1, bx1) - min(ax2, bx2)); dy = max(0, max(ay1, by1) - min(ay2, by2))
    return math.hypot(dx, dy) / ((a[2] + b[2]) / 2)

def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1]); x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    i = max(0, x2 - x1) * max(0, y2 - y1); return i / (a[2] * a[3] + b[2] * b[3] - i + 1e-6)

def skin_mask(img):
    ycc = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    m = cv2.inRange(ycc, (40, 135, 85), (240, 175, 130))
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

def torso_hist(img, box):
    x, y, w, h = [int(v) for v in box[:4]]; H, W = img.shape[:2]
    x1, x2 = max(0, x - w // 2), min(W, x + w + w // 2); y1, y2 = min(H - 1, y + int(h * 1.15)), min(H, y + int(h * 2.6))
    if y2 - y1 < 8 or x2 - x1 < 8: return None
    hsv = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [18, 8], [0, 180, 0, 256]); cv2.normalize(hist, hist, 1, 0, cv2.NORM_L1)
    return hist

def global_residual(prev, cur, region=None):
    """Farneback flow, then remove the best similarity transform (camera zoom/pan/rotate). Returns
    (local residual magnitude mean in px @180x320, |zoom-1|, |translation| px)."""
    flow = cv2.calcOpticalFlowFarneback(prev, cur, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    h, w = prev.shape; ys, xs = np.mgrid[4:h:8, 4:w:8]; p0 = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
    f = flow[ys.ravel(), xs.ravel()]; p1 = p0 + f
    M, _ = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=1.0)
    if M is None: return float(np.linalg.norm(f, axis=1).mean()), 0.0, 0.0, 0.0
    pred = p0 @ M[:, :2].T + M[:, 2]; res = np.linalg.norm(p1 - pred, axis=1)
    zoom = abs(math.sqrt(abs(np.linalg.det(M[:, :2]))) - 1); tr = float(np.linalg.norm(M[:, 2]))
    warped = cv2.warpAffine(prev, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    ph = np.abs(cur.astype(np.float32) - warped.astype(np.float32))[6:-6, 6:-6]
    if region is not None:   # subject box (x1,y1,x2,y2) in 180x320 coords: motion of the person, not the background
        x1, y1, x2, y2 = region; sel = (p0[:, 0] >= x1) & (p0[:, 0] <= x2) & (p0[:, 1] >= y1) & (p0[:, 1] <= y2)
        if sel.sum() >= 12: res = res[sel]
        sub = np.abs(cur.astype(np.float32) - warped.astype(np.float32))[max(0, y1):y2, max(0, x1):x2]
        if sub.size > 50: ph = sub
    return float(np.mean(res)), zoom, tr, float(np.percentile(ph, 95))

def warp0_explained(grays):
    """Ken Burns detector. Map frame 0 onto later frames with a homography (ORB features + RANSAC, then ECC
    refinement); a still / digital zoom / pan is fully explained (tiny residual), real footage is not
    (things move on their own, light changes). Returns p90 over the 2nd half of the take of the p95
    per-pixel residual (0-255 scale, blurred 180x320), or None."""
    if len(grays) < 6: return None
    g0 = grays[0]; orb = cv2.ORB_create(1500); k0, d0 = orb.detectAndCompute(g0, None); vals = []
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    for k in range(len(grays) // 2, len(grays), max(1, len(grays) // 12)):
        g = grays[k]; Hm = np.eye(3, dtype=np.float32)
        k1, d1 = orb.detectAndCompute(g, None)
        if d0 is not None and d1 is not None and len(k0) > 20 and len(k1) > 20:
            m = bf.match(d0, d1)
            if len(m) >= 12:
                A = np.float32([k0[x.queryIdx].pt for x in m]); B = np.float32([k1[x.trainIdx].pt for x in m])
                Hh, _ = cv2.findHomography(A, B, cv2.RANSAC, 2.0)
                if Hh is not None: Hm = Hh.astype(np.float32)
        try:   # refine: ECC finds W with g(W x) ~ g0(x); start from the feature estimate (inverse direction)
            _, Hm2 = cv2.findTransformECC(g0.astype(np.float32), g.astype(np.float32), Hm.copy(), cv2.MOTION_HOMOGRAPHY,
                                          (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-5), None, 5)
            Hm = Hm2
        except cv2.error:
            pass
        al = cv2.warpPerspective(g, Hm, (g.shape[1], g.shape[0]), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)
        d = np.abs(al.astype(np.float32) - g0.astype(np.float32))[16:-16, 12:-12]; vals.append(float(np.percentile(d, 95)))
    return round(float(np.percentile(vals, 90)), 2)

def run(video, spec=None, start=None, refs=None, audio=None, out=None):
    sp = dict(DEF); sp.update({k: v for k, v in (spec or {}).items() if k in DEF and v is not None})
    frames, fps = read_video(video)
    if not frames: return {"verdict": "FAIL", "reasons": ["UNREADABLE video"]}
    n = len(frames); dur = n / fps; step = max(1, round(fps / sp["sample_fps"])); idx = list(range(0, n, step))
    need = sp["need_s"] if sp["need_s"] is not None else max(0.5, dur - 0.2)
    faces = Faces(); refE = ref_embeddings(faces, refs or {})
    import mediapipe as mp
    hands = mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=4, model_complexity=1,
                                     min_detection_confidence=0.45, min_tracking_confidence=0.4)
    tiles = [(0.0, 0.45, 1.0, 1.0), (0.0, 0.55, 0.6, 1.0), (0.4, 0.55, 1.0, 1.0)]
    tile_hands = [mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=2, model_complexity=1,
                                           min_detection_confidence=0.5, min_tracking_confidence=0.4) for _ in tiles]
    mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=3, refine_landmarks=False,
                                           min_detection_confidence=0.4)
    per = []; first_face_emb = {}; hist0 = None; skin0 = None; face_union0 = None
    grays = [cv2.GaussianBlur(cv2.cvtColor(cv2.resize(frames[i], (180, 320)), cv2.COLOR_BGR2GRAY), (5, 5), 0) for i in idx]
    exp_faces = sp["faces_expected"]
    for k, i in enumerate(idx):
        img = frames[i]; H, W = img.shape[:2]; rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB); rec = {"t": round(i / fps, 3), "flags": []}
        # faces
        det = faces.detect(img); det = sorted(det, key=lambda r: -r[2] * r[3])
        mr = mesh.process(rgb); mboxes = []
        for fl in (mr.multi_face_landmarks or []):   # FaceMesh also finds profiles that YuNet misses
            xs = [p.x * W for p in fl.landmark]; ys = [p.y * H for p in fl.landmark]
            b = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
            cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
            if not any(d[0] <= cx <= d[0] + d[2] and d[1] <= cy <= d[1] + d[3] for d in det): mboxes.append(np.array(b + [0] * 10 + [0.5], np.float32))
        allf = det + mboxes; rec["faces"] = len(allf); rec["faces_yunet"] = len(det)
        strong = [d for d in det if d[-1] >= sp["extra_face_score"] and d[2] >= 0.06 * W]
        if exp_faces is None and k == 0: exp_faces = len(allf)
        if exp_faces and len(allf) < exp_faces: rec["flags"].append("FACE_LOST")
        if exp_faces is not None and k > 0 and len(strong) > exp_faces + sp["photo_faces"]: rec["flags"].append("EXTRA_FACE")
        if allf:
            b = max(allf, key=lambda r: r[2] * r[3]); sx, sy = 180 / W, 320 / H
            rec["_region"] = [int((b[0] - b[2] * 0.3) * sx), int((b[1] - b[3] * 0.3) * sy), int((b[0] + b[2] * 1.3) * sx), int((b[1] + b[3] * 1.6) * sy)]
        if len(allf) >= 2:
            g = min(box_gap(allf[a], allf[b]) for a in range(len(allf)) for b in range(a + 1, len(allf)))
            rec["face_gap"] = round(g, 3)
            if g < sp["min_face_gap"] and (exp_faces or 0) >= 2: rec["flags"].append("FACES_CLOSE")
        sims = {}
        for j, r in enumerate(det[:3]):
            e = faces.embed(img, r)
            if refE:
                for nm, re_ in refE.items(): sims[nm] = max(sims.get(nm, -1), round(faces.cos(e, re_), 3))
            if j == 0: rec["_emb"] = e
        if sims: rec["ref_sim"] = sims
        # wardrobe (torso colour under the biggest face)
        if det:
            hh = torso_hist(img, det[0])
            if hh is not None:
                if hist0 is None: hist0 = hh
                dist = float(cv2.compareHist(hist0, hh, cv2.HISTCMP_BHATTACHARYYA)); rec["wardrobe_d"] = round(dist, 3)
                if dist > sp["wardrobe_max"]: rec["flags"].append("WARDROBE_DRIFT")
        # hands
        hr = hands.process(rgb); nh = len(hr.multi_hand_landmarks or [])
        if not nh and not sp["hands_allowed"] and sp["hand_tiles"]:   # off by default: 2x-zoom tiles found hands in 3 good takes (ep1 test)
            for (x1, y1, x2, y2), th in zip(tiles, tile_hands):
                crop = rgb[int(y1 * H):int(y2 * H), int(x1 * W):int(x2 * W)]
                tr = th.process(np.ascontiguousarray(crop))
                if tr.multi_hand_landmarks: nh = len(tr.multi_hand_landmarks); rec["hand_tile"] = [x1, y1, x2, y2]; hr = tr; break
        rec["hands"] = nh
        if nh:
            rec["hand_score"] = round(max(c.classification[0].score for c in hr.multi_handedness), 3)
            if not sp["hands_allowed"]: rec["flags"].append("HAND")
            else:   # allowed: anatomy sanity (finger segment ratios vs the palm; melting hands give absurd ratios)
                for lm in hr.multi_hand_landmarks:
                    P = np.array([[p.x * W, p.y * H] for p in lm.landmark]); palm = np.linalg.norm(P[0] - P[9]) + 1e-6
                    segs = [np.linalg.norm(P[a] - P[b]) / palm for a, b in ((5, 8), (9, 12), (13, 16), (17, 20), (1, 4))]
                    if max(segs) > 1.6 or min(segs) < 0.15: rec["flags"].append("HAND_ANATOMY")
                    rec.setdefault("finger_ratios", []).append([round(s, 2) for s in segs])
        # new skin entering (hands the detector misses): skin area outside the face+neck zones vs the first frame
        sm = skin_mask(cv2.resize(img, (180, 320))); zone = np.zeros_like(sm)
        for r in det:
            x, y, w, h = [int(v * 180 / W) if q % 2 == 0 else int(v * 320 / H) for q, v in enumerate(r[:4])]
            cv2.rectangle(zone, (x - w // 3, y - h // 3), (x + w + w // 3, y + int(h * 1.8)), 255, -1)
        out_skin = cv2.bitwise_and(sm, cv2.bitwise_not(zone))
        frac = float((out_skin > 0).mean())
        if skin0 is None: skin0 = frac
        rec["skin_out"] = round(frac, 4)
        if frac - skin0 > sp["skin_growth_max"]: rec["flags"].append("SKIN_ENTERING")
        # mouth
        if mr.multi_face_landmarks:
            L = mr.multi_face_landmarks[0].landmark
            op = math.dist((L[13].x * W, L[13].y * H), (L[14].x * W, L[14].y * H)) / (math.dist((L[61].x * W, L[61].y * H), (L[291].x * W, L[291].y * H)) + 1e-6)
            rec["mouth"] = round(op, 3)
        # motion
        if k > 0:
            loc, zm, tr, ph = global_residual(grays[k - 1], grays[k], rec.get("_region")); rec["local_motion"] = round(loc, 3); rec["zoom"] = round(zm, 4); rec["pan"] = round(tr, 3); rec["photometric"] = round(ph, 2)
            d = float(np.abs(grays[k].astype(np.float32) - grays[k - 1].astype(np.float32)).mean()); rec["diff"] = round(d, 2)
        per.append(rec)
    hands.close(); mesh.close(); [th.close() for th in tile_hands]
    # temporal checks
    rep = {"video": str(video), "frames": n, "fps": round(fps, 3), "duration_s": round(dur, 3), "spec": sp, "reasons": [], "per_frame": per}
    lm = np.array([r.get("local_motion", np.nan) for r in per[1:]]); zm = np.array([r.get("zoom", 0) for r in per[1:]])
    diffs = np.array([r.get("diff", 0) for r in per[1:]])
    rep["local_motion_mean"] = round(float(np.nanmean(lm)), 3) if len(lm) else 0
    rep["local_motion_p90"] = round(float(np.nanpercentile(lm, 90)), 3) if len(lm) else 0
    rep["zoom_mean"] = round(float(np.mean(zm)), 4) if len(zm) else 0
    phm = np.array([r.get("photometric", 0) for r in per[1:]]); rep["photometric_mean"] = round(float(phm.mean()), 3) if len(phm) else 0
    if len(lm) and rep["local_motion_mean"] < sp["min_local_motion"] and rep["photometric_mean"] < sp["min_photometric"]:
        rep["reasons"].append(("KEN_BURNS (camera move only)" if rep["zoom_mean"] > 0.002 or np.mean([r.get("pan", 0) for r in per[1:]]) > 0.3 else "STILL (no motion)") + f" local={rep['local_motion_mean']}")
    kb = warp0_explained(grays); rep["warp0_residual"] = kb
    if kb is not None and kb < sp["min_warp0_residual"] and not any(x.startswith(("STILL", "KEN_BURNS")) for x in rep["reasons"]):
        rep["reasons"].append(f"KEN_BURNS/STILL: every frame is frame 0 re-framed (residual {kb} < {sp['min_warp0_residual']})")
    med = float(np.median(diffs)) if len(diffs) else 0
    cuts = [per[k + 1]["t"] for k, d in enumerate(diffs) if d > max(12.0, 6 * med)]
    if cuts: rep["reasons"].append(f"HARD_CUT at {cuts}")
    if refE:
        best = {}
        for r in per:
            for nm, v in r.get("ref_sim", {}).items(): best.setdefault(nm, []).append(v)
        rep["identity_ref_median"] = {nm: round(float(np.median(v)), 3) for nm, v in best.items()}
        for nm, v in rep["identity_ref_median"].items():
            if v < sp["identity_min"]: rep["reasons"].append(f"IDENTITY_LOW {nm} median={v} (< {sp['identity_min']}) vs the reference sheet")
    embs = [(r, r.pop("_emb")) for r in per if "_emb" in r]
    if len(embs) >= 3:   # identity anchor = medoid of the take's own faces (frame 0 can be a fade / cover image)
        E = np.array([e / (np.linalg.norm(e) + 1e-9) for _, e in embs]); S = E @ E.T; med = E[int(np.argmax(S.sum(1)))]
        for (r, _), v in zip(embs, E @ med): r["self_sim"] = round(float(v), 3)
    ss = [(r["t"], r["self_sim"]) for r in per if "self_sim" in r]
    if len(ss) >= 5:
        v = np.array([x for _, x in ss]); rm = np.array([np.median(v[max(0, i - 2): i + 3]) for i in range(len(v))]); rep["identity_self_min"] = round(float(rm.min()), 3)
        for (t, _), m in zip(ss, rm):
            if m < sp["identity_drift_min"]:
                for r in per:
                    if r["t"] == t: r["flags"].append("IDENTITY_DRIFT")
    mouths = np.array([r["mouth"] for r in per if "mouth" in r])
    if len(mouths) >= 4:
        rng = float(np.percentile(mouths, 95) - np.percentile(mouths, 5)); rep["mouth_range"] = round(rng, 3); rep["mouth_max"] = round(float(mouths.max()), 3)
        if sp["mouth"] == "closed":
            for r in per:
                if "mouth" in r and r["mouth"] > sp["mouth_open_max"]: r["flags"].append("MOUTH_OPEN")
            if rng > sp["mouth_range_max"]:
                # mark the frames that move away from the take's resting mouth
                rest = float(np.percentile(mouths, 20))
                for r in per:
                    if "mouth" in r and abs(r["mouth"] - rest) > sp["mouth_range_max"]: r["flags"].append("MOUTHING")
    if sp["mouth"] == "lipsync" and audio:
        rep["lipsync_note"] = "lip-sync scoring needs pipeline/sync_check.py (face_alignment); not run here"
    # clean windows (frame-level flags only; motion/cut are take-level)
    for r in per: r.pop("_region", None)
    best, cur_s = (0.0, 0.0, 0.0), None
    for k, r in enumerate(per + [{"t": dur, "flags": ["END"]}]):
        isbad = any(f not in ADVISORY for f in r["flags"])
        if not isbad and cur_s is None: cur_s = r["t"]
        if isbad and cur_s is not None:
            end = r["t"] if k < len(per) else dur
            if end - cur_s > best[2]: best = (cur_s, end, end - cur_s)
            cur_s = None
    rep["clean_window"] = [round(best[0], 3), round(best[1], 3)]
    from collections import Counter
    cnt = Counter(f for r in per for f in r["flags"]); rep["flag_counts"] = dict(cnt)
    first_bad = {}
    for r in per:
        for f in r["flags"]: first_bad.setdefault(f, r["t"])
    rep["first_bad_t"] = first_bad
    # a flag must persist >= 2 samples (0.25 s) to count; single-sample blips are noise
    persistent = [f for f, c in cnt.items() if c >= (3 if f == "EXTRA_FACE" else 2) and f not in ADVISORY]
    rep["advisory"] = {f: c for f, c in cnt.items() if f in ADVISORY}
    take_level = list(rep["reasons"])
    if persistent: rep["reasons"] += [f"{f} from {first_bad[f]}s ({cnt[f]} samples)" for f in sorted(persistent, key=lambda f: first_bad[f])]
    if not rep["reasons"]: rep["verdict"] = "REVIEW" if sp["hands_allowed"] else "PASS"   # hand inserts: anatomy needs eyes (ep1 test)
    elif not take_level and best[2] >= need: rep["verdict"] = "TRIM"
    else: rep["verdict"] = "FAIL"
    rep["need_s"] = round(need, 3)
    if out: Path(out).write_text(json.dumps(rep, indent=1))
    return rep

def summary(rep):
    s = f"{rep['verdict']:5} {Path(rep['video']).name} dur={rep.get('duration_s')} motion={rep.get('local_motion_mean')} clean={rep.get('clean_window')}"
    return s + ("  | " + "; ".join(rep["reasons"]) if rep["reasons"] else "")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("video"); ap.add_argument("--spec"); ap.add_argument("--start")
    ap.add_argument("--refs", nargs="*", default=[]); ap.add_argument("--audio"); ap.add_argument("--out")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text()) if a.spec and Path(a.spec).exists() else (json.loads(a.spec) if a.spec else {})
    refs = dict(r.split("=", 1) for r in a.refs)
    rep = run(a.video, spec, a.start, refs, a.audio, a.out); print(summary(rep))
    sys.exit({"PASS": 0, "TRIM": 0, "REVIEW": 2}.get(rep["verdict"], 3))
