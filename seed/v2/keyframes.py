#!/usr/bin/env python3
"""Locked first/last keyframes per shot, made from the approved character sheets with a fixed wardrobe per scene.
  python keyframes.py brief    SHOTS.json [--ids a,b]       -> keyframes/briefs/<id>.json + .md (GenerateImage brief:
                                                               refs = character sheets + place sheet + the scene's
                                                               approved keyframe; framing template; wardrobe lock)
  python keyframes.py check    IMAGE --shot ID SHOTS.json   -> still checks: 0 hands, visible-face count, face gap,
                                                               identity vs sheet (SFace), torso swatch vs scene swatch
  python keyframes.py register IMAGE --shot ID --role start|end SHOTS.json   (only if check passes; --force to override)
                                                            -> keyframes/<scene>/<id>_<role>.png (720x1280), sha256 in
                                                               keyframes/manifest.json, shot.start_frame/end_frame set
  python keyframes.py derive   --shot ID --mode pin|drift SHOTS.json         -> end keyframe from the registered start
Run with v2/qcvenv/bin/python (MediaPipe + OpenCV)."""
import argparse, json, os, re, shutil, sys, time
from pathlib import Path
import numpy as np, cv2
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import keyframe_ops as ko
PROJECT = Path(os.environ.get("SATRCUT_PROJECT", str(Path.cwd())))
OWN = HERE.parent
KF = HERE / "keyframes"; MAN = KF / "manifest.json"

FRAMING = {
 "single_cu": "Vertical 9:16 photograph, close-up framed from the collarbones up: the face fills the upper-middle third, shoulders touch the bottom edge, only the face, neck and collar are in frame.",
 "ots": "Vertical 9:16 over-the-shoulder photograph: the back of {back}'s head and shoulder fill the lower left third, out of focus; {face}'s face is sharp, framed from the chest up, at least one face-width of space between their heads.",
 "two_shot_wide": "Vertical 9:16 medium two-shot from the waist up, the two people stand an arm's length apart with clear space between their faces; arms relaxed at their sides below the bottom edge of the frame.",
 "insert": "Vertical 9:16 macro insert photograph of the object only, filling the frame, shallow depth of field, no people.",
 "establishing": "Vertical 9:16 wide establishing photograph of the place, empty of people, all doors closed, vehicles parked and filling the frame or absent.",
}

def load(p): return json.loads(Path(p).read_text())
def man():
    return load(MAN) if MAN.exists() else {"keyframes": []}
def shot_of(doc, sid): return next(s for s in doc["shots"] if s["id"] == sid)

def wardrobe(doc, s):
    sc = doc.get("scenes", {}).get(s.get("scene", ""), {}).get("wardrobe", {})
    return {n: sc.get(n) or sc.get(n.split(" (")[0]) or doc["characters"].get(n, {}).get("look", "") for n in s.get("in_frame", [])}

def cmd_brief(a):
    doc = load(a.shots); KF.joinpath("briefs").mkdir(parents=True, exist_ok=True); m = man()
    for s in doc["shots"]:
        if a.ids and s["id"] not in a.ids.split(","): continue
        st = s.get("shot_type") or ("insert" if not s.get("in_frame") else "single_cu")
        fv = s.get("faces_visible", s.get("in_frame", [])); back = [n for n in s.get("in_frame", []) if n not in fv]
        frame = FRAMING.get(st, FRAMING["single_cu"]).format(back=(back or ["the other person"])[0].split(" (")[0], face=(fv or ["the actor"])[0].split(" (")[0])
        refs = [doc["characters"][n]["sheet"] for n in s.get("in_frame", []) if doc["characters"].get(n, {}).get("sheet")]
        place = doc.get("places", {}).get(s.get("place", ""), {})
        if place.get("sheet"): refs.append(place["sheet"])
        same_scene = [k["path"] for k in m["keyframes"] if k.get("scene") == s.get("scene") and k["role"] == "start"]
        if same_scene: refs.append(same_scene[-1])   # continuity: light, set and wardrobe from the scene's first approved keyframe
        wd = wardrobe(doc, s)
        who = " ".join(f"{n.split(' (')[0]}: exactly the person in the reference sheet, wearing {w}." for n, w in wd.items())
        mood = s.get("keyframe_mood") or s.get("action", "")
        prompt = (f"{frame} {doc.get('film_look', '')} Place: {place.get('desc', s.get('place', ''))}. {who} "
                  f"Moment: {mood} Expression and light only; lips pressed together unless the shot is a talking close-up.").strip()
        b = {"shot": s["id"], "scene": s.get("scene"), "shot_type": st, "reference_images": refs, "wardrobe": wd, "prompt": re.sub(r"\s+", " ", prompt),
             "aspect": "9:16", "anchor": s.get("anchor", "pin"),
             "rules": ["no hands/arms/wrists in frame (crop them out after generation if needed)", "faces of different people >= 1 face width apart",
                       "doors closed, nobody in the background", "no readable text", "same wardrobe as the scene lock"]}
        (KF / "briefs" / f"{s['id']}.json").write_text(json.dumps(b, indent=1))
        (KF / "briefs" / f"{s['id']}.md").write_text(f"# keyframe brief {s['id']} ({st})\nrefs: {refs}\n\n{b['prompt']}\n")
        print("brief", s["id"], st, len(refs), "refs")

def still_checks(img_path, doc, s):
    import qc_gate, mediapipe as mp
    img = cv2.imread(str(img_path)); H, W = img.shape[:2]; rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB); res = {"flags": []}
    with mp.solutions.hands.Hands(static_image_mode=True, max_num_hands=4, min_detection_confidence=0.5) as hands:
        hr = hands.process(rgb); res["hands"] = len(hr.multi_hand_landmarks or [])
    if res["hands"] and not s.get("allow_hands"): res["flags"].append(f"HANDS_IN_KEYFRAME ({res['hands']})")
    F = qc_gate.Faces(); det = sorted(F.detect(img), key=lambda r: -r[2] * r[3]); res["faces"] = len(det)
    fv = s.get("faces_visible", s.get("in_frame", []))
    if len(det) != len(fv) + s.get("photo_faces", 0): res["flags"].append(f"FACE_COUNT {len(det)} != {len(fv)}")
    if len(det) >= 2:
        g = min(qc_gate.box_gap(det[i], det[j]) for i in range(len(det)) for j in range(i + 1, len(det))); res["face_gap"] = round(g, 2)
        if g < s.get("min_face_gap", 0.45) and len(fv) >= 2: res["flags"].append(f"FACES_CLOSE gap={g:.2f}")
    refs = {n: str(PROJECT / doc["characters"][n]["sheet"]) for n in fv if doc["characters"].get(n, {}).get("sheet")}
    refE = qc_gate.ref_embeddings(F, refs); sims = {}
    for r in det[:3]:
        e = F.embed(img, r)
        for n, re_ in refE.items(): sims[n] = max(sims.get(n, -1), round(F.cos(e, re_), 3))
    res["identity"] = sims
    for n, v in sims.items():
        if v < 0.36: res["flags"].append(f"IDENTITY {n}={v} (< 0.36 on a still)")
    # scene swatch: torso colour vs the scene's first registered keyframe of the same character
    m = man(); same = [k for k in m["keyframes"] if k.get("scene") == s.get("scene") and k.get("swatch")]
    if det:
        h = qc_gate.torso_hist(img, det[0])
        if h is not None:
            res["swatch"] = h.flatten().round(5).tolist()
            if same:
                d = float(cv2.compareHist(np.array(same[0]["swatch"], np.float32).reshape(18, 8), h, cv2.HISTCMP_BHATTACHARYYA)); res["swatch_d"] = round(d, 3)
                if d > 0.5: res["flags"].append(f"WARDROBE vs scene keyframe d={d:.2f}")
    res["ok"] = not res["flags"]; return res

def cmd_check(a):
    doc = load(a.shots); r = still_checks(a.image, doc, shot_of(doc, a.shot)); r.pop("swatch", None); print(json.dumps(r, indent=1)); sys.exit(0 if r["ok"] else 3)

def register(path, doc, s, role, checks, shots_path):
    scene = re.sub(r"[^A-Za-z0-9]+", "_", s.get("scene", "scene")).strip("_")
    dst = KF / scene / f"{s['id']}_{role}.png"; dst.parent.mkdir(parents=True, exist_ok=True); ko.fit(path, dst)
    rel = str(dst.relative_to(OWN)); m = man(); m["keyframes"] = [k for k in m["keyframes"] if k["path"] != rel]
    m["keyframes"].append({"path": rel, "sha256": ko.sha256(dst), "shot": s["id"], "role": role, "scene": s.get("scene"), "wardrobe": wardrobe(doc, s),
                           "source": str(path), "checks": {k: v for k, v in checks.items() if k != "swatch"}, "swatch": checks.get("swatch"), "registered": time.strftime("%F %T")})
    MAN.parent.mkdir(parents=True, exist_ok=True); MAN.write_text(json.dumps(m, indent=1))
    s["start_frame" if role == "start" else "end_frame"] = rel
    Path(shots_path).write_text(json.dumps(doc, indent=1, ensure_ascii=False)); print("registered", rel); return dst

def cmd_register(a):
    doc = load(a.shots); s = shot_of(doc, a.shot); r = still_checks(a.image, doc, s)
    if not r["ok"] and not a.force: print("NOT registered:", r["flags"]); sys.exit(3)
    register(a.image, doc, s, a.role, r, a.shots)

def cmd_derive(a):
    doc = load(a.shots); s = shot_of(doc, a.shot); st = OWN / s["start_frame"]; tmp = KF / f"_tmp_{s['id']}_end.png"
    if a.mode == "pin": shutil.copy(st, tmp)
    else: ko.drift_end(st, tmp, scale=a.scale)
    s["anchor"] = a.mode; register(tmp, doc, s, "end", {"derived": a.mode}, a.shots); tmp.unlink()

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="c", required=True)
    p = sp.add_parser("brief"); p.add_argument("shots"); p.add_argument("--ids", default="")
    p = sp.add_parser("check"); p.add_argument("image"); p.add_argument("--shot", required=True); p.add_argument("shots")
    p = sp.add_parser("register"); p.add_argument("image"); p.add_argument("--shot", required=True); p.add_argument("--role", choices=["start", "end"], default="start"); p.add_argument("--force", action="store_true"); p.add_argument("shots")
    p = sp.add_parser("derive"); p.add_argument("--shot", required=True); p.add_argument("--mode", choices=["pin", "drift"], default="pin"); p.add_argument("--scale", type=float, default=1.04); p.add_argument("shots")
    a = ap.parse_args(); globals()["cmd_" + a.c](a)

if __name__ == "__main__":
    main()
