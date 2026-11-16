#!/usr/bin/env python3
"""SatrCut v2 shot-plan linter. Bans the shot patterns that failed in ep1 BEFORE anything renders.
   python3 shotlint.py SHOTS.json [--episode N] [--write-qc] [--allow-lipsync] [--json report.json]
Exit 1 when there are ERRORs. Every rule cites the ep1 failure it comes from (production/ROOT_CAUSES.md).

Why words matter: Wan2.2 A14B + lightx2v 4-step runs at cfg=1.0, so the NEGATIVE prompt is ignored and every
noun in the POSITIVE prompt is drawn - including "hands out of frame", "no steering wheel", "ring on his hand".

Shot fields used (v1 shots stay valid; v2 fields in [brackets]):
  id scene method frames fps in_frame place speaker line subtitle action camera start_frame [end_frame]
  [anchor: pin|drift|keyed] [motion: what moves that is not the camera] [line_mode: os|listener|vo|insert|lipsync_cu]
  [line_audio: wav path] [faces_visible: [names]] [shot_type: single_cu|ots|two_shot|insert|establishing]
  [allow_hands: "reason"] [photo_faces: n] [char_loras]
Doc fields: [scenes: {scene: {wardrobe: {char: look}, wardrobe_change_shown: bool}}] characters places
"""
import argparse, json, re, sys
from pathlib import Path

HAND = r"\b(hands?|fingers?|fingertips?|palms?|wrists?|knuckles?|thumbs?|fists?|grips?|gripping|grabs?|grabbing|clutch\w*|holds?|holding|clasp\w*|touch\w*|strok\w*|caress\w*|trac(e|es|ing)|rings?|watch|bracelet|bandana|steering|wheel|gear ?shift\w*|stalk|lifts?|lifting|picks? up|sets? (down|the)|pulls?|pushes?|slides?|presses|pressed|reaches|reaching|mugs?|cups?|phone to (his|her) ear|cover\w* (his|her) (face|mouth))\b"
CONTACT = r"\b(kiss\w*|lips? (touch|almost|brush|hover|meet|graze)\w*|almost touching|nearly touching|lean(s|ing)? (in|close|toward)|forehead|nose[- ]to[- ]nose|cheek|embrac\w*|hug\w*|pulls? (him|her) (in|close)|close together|a breath from|whisper\w* in (his|her) ear|nuzzl\w*|face to face)\b"
NEGATION = r"\b(no|not|never|without|doesn'?t|does not|don'?t|isn'?t|nobody|out of frame|off[- ]screen)\b"
DOOR = r"\b(door (opens|swings|creaks|is open|open\w*)|opens the door|swings (open|inward|outward)|walks? in|enters|entering|steps? in|comes? in|appears? behind|crowd|passers?-?by|pedestrians?|bystanders?|people in the background|someone)\b"
VEHICLE = r"\b(truck|lorry|bus|traffic|cars?|vehicles?|trailer|ambulance)\b.{0,60}\b(distant|in the distance|far|approach\w*|drives?|driving|speed\w*|across the lanes|jackknif\w*|brakes?|swerv\w*|skid\w*|races?|racing)\b"
WIPER = r"\bwipers?\b"
TEXT = r"\b(signs?|signage|text|labels?|patch(es)?|logos?|captions?|headlines?|reads? [\"']|screen shows|written|lettering|numbers? on)\b"
PHOTO = r"\b(photo(graph)?s?|picture|portrait|framed)\b"
CAMERA_MOVE = r"\b(push[- ]?in|pull[- ]?(out|back)|dolly|pans?|panning|zoom\w*|orbit\w*|tracking|crane|tilts?|handheld|whip)\b"
HAND_DETAIL = r"\b(ring|rings|watch|bracelet|wrist|hand|hands|fingers?|bandana)\b"

def find(rx, text): return sorted(set(m.group(0).lower() for m in re.finditer(rx, text or "", re.I)))

def lint(doc, episode=None, allow_lipsync=False, root=Path(".")):
    out = []   # (level, shot, rule, message)
    E = lambda sid, rule, msg: out.append(("ERROR", sid, rule, msg))
    W = lambda sid, rule, msg: out.append(("WARN", sid, rule, msg))
    chars = doc.get("characters", {}); scenes = doc.get("scenes", {})
    ep = episode or doc.get("episode") or 0
    try: ep = int(re.sub(r"\D", "", str(ep)) or 0)
    except ValueError: ep = 0
    for name, c in chars.items():
        bad = find(HAND_DETAIL, c.get("unique_detail", "") + " " + c.get("look", ""))
        if bad: E(f"char:{name}", "L11 HAND_DETAIL", f"character sheet puts {bad} in every prompt -> hands get drawn (1-01b t01, 2-04b/2-08b/3-04b2 fix1-2). Use a face/neck/collar detail (earring, scar, necklace, collar pin).")
    total = 0.0; prev_wardrobe = {}
    for s in doc["shots"]:
        sid = s["id"]; txt = " ".join(str(s.get(k, "")) for k in ("action", "camera", "setting", "motion"))
        prompt = s.get("prompt", "") + " " + txt
        method = s.get("method", s.get("model", "")); fps = s.get("fps", 16); frames = s.get("frames", 81)
        dur = s.get("duration_s") or frames / fps; total += dur
        infr = s.get("in_frame", []); faces_vis = s.get("faces_visible", infr)
        st = s.get("shot_type", "")
        # L01 hands
        h = find(HAND, prompt)
        if h and not s.get("allow_hands"):
            E(sid, "L01 HANDS", f"prompt names {h}: hands appear and melt (32 of 78 ep1 rejects). Frame from the collarbones up and describe eyes/breath/light instead; or set allow_hands with a reason for a deliberate insert (gate verdict will be REVIEW).")
        elif h:
            W(sid, "L01 HANDS", f"deliberate hand insert ({s['allow_hands']}): pin start=end, two hands max, no wheel/gear/ring; needs a human look (2-09 v1-v3, 2-09b failed).")
        # L02 negations at cfg 1
        n = find(NEGATION, prompt)
        if n and method in ("a14b", ""):
            E(sid, "L02 NEGATION", f"positive prompt contains {n}; A14B+lightx2v runs cfg=1 so negatives are ignored and negated nouns get drawn. Say what IS there (\"lips pressed together\", \"empty corridor\").")
        # L03 contact / faces
        c = find(CONTACT, prompt)
        if c:
            lvl = E if (ep and ep < 10) or len(faces_vis) >= 2 else W
            lvl(sid, "L03 CONTACT", f"{c}: two faces closing in merge/melt (4-08 t01/t02, 3-04c, 3-04d2, 4-08b) and ep1-9 has no kisses. Keep >= half a face width between faces; play intimacy as a single close-up + reaction.")
        if len(faces_vis) >= 2 and st not in ("two_shot_wide", "ots"):
            E(sid, "L03 TWO_FACES", f"{len(faces_vis)} visible faces in a close frame (2-03 t02/t03 mouth-sharing, 3-04c, 4-08b). Use shot_type ots (one face visible) or two_shot_wide with >= 0.45 face-width gap, or split into singles.")
        # L04 dialogue placement
        has_line = bool(s.get("line") or s.get("subtitle") or s.get("audio"))
        if has_line:
            mode = s.get("line_mode")
            spk = (s.get("speaker") or "").split(" (")[0].strip().title()
            on_screen = any(spk and n.split(" (")[0].lower() == spk.lower() for n in faces_vis)
            if not mode:
                E(sid, "L04 LINE_MODE", "dialogue shot without line_mode (os|listener|vo|insert|lipsync_cu). Ep1: lines landed on closed mouths (1-01) and O.S. lines went missing after edits (Nov 13 audit).")
            elif mode == "lipsync_cu" and not allow_lipsync:
                E(sid, "L04 LIPSYNC_BANNED", "on-screen lip-sync: 0 of 12 InfiniteTalk/MuseTalk/i2v talking takes passed strict review. Put the line O.S. over the listener, an OTS from behind the speaker, or an insert.")
            elif mode in ("os", "listener", "vo", "insert") and on_screen:
                E(sid, "L04 SPEAKER_VISIBLE", f"{spk}'s line is O.S./listener but {spk}'s face is in frame: the viewer sees a closed mouth while the line plays (reviewer 19:01, 4-06b/1-01). Remove the speaker from faces_visible (back of head / out of frame) or move the line.")
            la = s.get("line_audio") or s.get("audio") or next((x.get("file") for x in s.get("sfx", []) if "line" in x.get("note", "").lower() or "O.S." in x.get("note", "") or "V.O." in x.get("note", "")), None)
            if s.get("subtitle") and not la:
                E(sid, "L04 LINE_AUDIO", "subtitle with no line audio attached to this shot (Nov 13 audit: O.S. shots had no line audio and subtitles shifted). Add line_audio or an sfx entry with note 'line'.")
            elif la and not (root / la).exists() and not Path(la).exists():
                W(sid, "L04 LINE_AUDIO", f"line audio file not found: {la}")
            if faces_vis and mode in ("listener", "os") and not re.search(r"\b(lips (pressed|sealed|closed)|mouth (closed|shut)|jaw (set|clenched|tight))\b", prompt, re.I):
                W(sid, "L04 LISTENER_MOUTH", "listener shot should say 'lips pressed together' / 'jaw set' in the positive prompt (2c reaction, 4-02 v1 mouthed with no audio).")
        # L05 doors / stray people
        d = find(DOOR, prompt)
        if d: E(sid, "L05 DOOR_PEOPLE", f"{d}: doors open and strangers appear (5-01 s101). Show the closed door; carry entrances with sound + the next shot.")
        if re.search(r"\bdoor\b", prompt, re.I) and not s.get("anchor") and not s.get("end_frame"):
            E(sid, "L05 DOOR_ANCHOR", "a door in frame needs a pinned end keyframe (closed door) so it can't swing open.")
        # L06 vehicles / wipers
        v = find(VEHICLE, prompt)
        if v: E(sid, "L06 VEHICLE_MOTION", f"{v}: moving/distant vehicles duplicate wheels and melt (shot01 ext, 2-03b, 2-01 t02). Use an interior POV with the vehicle filling the frame, pinned, + SFX.")
        if find(WIPER, prompt): E(sid, "L06 WIPERS", "wipers morph and smear across faces (2-03 t02/t03, shot02b, shot03). Park them out of frame / use a side-window angle.")
        # L07 in-camera text
        t = find(TEXT, prompt)
        if t: W(sid, "L07 TEXT", f"{t}: generated text comes out as gibberish (shot01, 2b patch, 2c visor, shot05 bandana). Do text as an edit overlay.")
        # L08 photos with faces
        if find(PHOTO, prompt) and not s.get("photo_faces"):
            W(sid, "L08 PHOTO_FACE", "a photo prop: its face animates in i2v (4-01 v1). Set photo_faces: 1 (gate) and pin start=end; or keep the photo face-down / out of focus.")
        # L09 anchors
        if method == "a14b":
            if not s.get("start_frame"): E(sid, "L09 START", "no start keyframe")
            if not s.get("end_frame") and s.get("anchor") not in ("pin", "drift", "keyed"):
                E(sid, "L09 END_ANCHOR", "no end keyframe / anchor: actions drift without one (2-09 v3 role swap, 2-01 t02 no jackknife, hands entering late). Use anchor pin (start=end) or drift, or a keyed end frame.")
            kf = doc.get("_keyframes", {})
            for role in ("start_frame", "end_frame"):
                f = s.get(role)
                if f and kf and f not in kf: W(sid, "L09 UNREGISTERED", f"{role} {f} is not in keyframes/manifest.json (approved keyframe with scene wardrobe)")
        # L10 wardrobe lock per scene
        sc = scenes.get(s.get("scene", ""), {})
        for n in infr:
            base = n.split(" (")[0]
            if scenes and n not in sc.get("wardrobe", {}) and base not in sc.get("wardrobe", {}):
                E(sid, "L10 WARDROBE", f"scene '{s.get('scene')}' has no locked wardrobe for {n} (Sam's uniform -> leather jacket confused the reviewer: 3-04c, 3-04d2).")
            look = chars.get(n, {}).get("look", "")
            if prev_wardrobe.get(base) and prev_wardrobe[base][0] != look and not sc.get("wardrobe_change_shown") and prev_wardrobe[base][1] != s.get("scene"):
                W(sid, "L10 WARDROBE_CHANGE", f"{base} changes look from the previous scene ({prev_wardrobe[base][1]}); show the change (caption card / establishing / dialogue) and set scenes[scene].wardrobe_change_shown.")
            prev_wardrobe[base] = (look, s.get("scene"))
        # L12 length
        if frames % 4 != 1: E(sid, "L12 FRAMES", "frames must be 4n+1")
        if method == "a14b" and frames > 81: E(sid, "L12 LENGTH", "A14B: keep <= 81 frames (5.06 s); split the beat")
        if dur < 1.5 and not s.get("allow_short"): W(sid, "L12 SHORT", f"{dur:.2f} s: very short shots read as a glitch (2-03c 1.2 s, 4-03 crop 1.2 s)")
        # L13 LoRA
        for n in faces_vis:
            reg = doc.get("_lora_registry", {}).get(n.split(" (")[0])
            if reg and reg.get("approved") and not any(reg["file"] in x.get("name", "") for x in s.get("char_loras", [])):
                W(sid, "L13 LORA", f"{n} has an approved LoRA ({reg['file']}) that this shot doesn't load")
        # L14 pin + camera move / motion plan
        cm = find(CAMERA_MOVE, s.get("camera", "") + " " + s.get("action", ""))
        if s.get("anchor") == "pin" and cm:
            E(sid, "L14 PIN_CAMERA", f"start=end pin but the camera {cm}: the shot must return to its first frame, so the move reverses or freezes. Use anchor drift, or a locked-off camera.")
        if s.get("anchor") == "pin" and not s.get("motion"):
            W(sid, "L14 MOTION_PLAN", "pinned shot without a 'motion' field (what visibly moves: rain, light, breath, eyes). Pinned takes went near-still (3-04b2 s101, 5-01 s303).")
    tgt = doc.get("target_runtime_s")
    if tgt and abs(total - tgt) > 0.12 * tgt:
        out.append(("WARN", "episode", "L15 RUNTIME", f"planned shots sum to {total:.1f} s vs target {tgt} s"))
    return out, total

def qc_spec(doc, s):
    """Gate spec the PC runner uses for this shot (qc_gate.py fields)."""
    fv = s.get("faces_visible", s.get("in_frame", []))
    return {"hands_allowed": bool(s.get("allow_hands")), "faces_expected": len(fv), "photo_faces": s.get("photo_faces", 0),
            "mouth": "free" if s.get("line_mode") == "lipsync_cu" or s.get("mouth") == "free" else "closed",
            "min_face_gap": s.get("min_face_gap", 0.45), "need_s": s.get("need_s"),
            "refs": {n.split(" (")[0]: doc["characters"][n]["sheet"] for n in fv if n in doc.get("characters", {}) and doc["characters"][n].get("sheet")}}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("shots"); ap.add_argument("--episode", type=int); ap.add_argument("--write-qc", action="store_true")
    ap.add_argument("--allow-lipsync", action="store_true"); ap.add_argument("--json"); ap.add_argument("--root", default=".")
    ap.add_argument("--keyframes", default=str(Path(__file__).resolve().parent / "keyframes" / "manifest.json"))
    ap.add_argument("--lora-registry", default=str(Path(__file__).resolve().parent / "lora_registry.json"))
    a = ap.parse_args(); p = Path(a.shots); doc = json.loads(p.read_text())
    if Path(a.keyframes).exists(): doc["_keyframes"] = {k["path"]: k for k in json.loads(Path(a.keyframes).read_text()).get("keyframes", [])}
    if Path(a.lora_registry).exists(): doc["_lora_registry"] = json.loads(Path(a.lora_registry).read_text())
    res, total = lint(doc, a.episode, a.allow_lipsync, Path(a.root))
    for lvl, sid, rule, msg in res: print(f"{lvl:5} {sid:10} {rule:22} {msg}")
    ne = sum(r[0] == "ERROR" for r in res); nw = len(res) - ne
    bad_shots = sorted(set(r[1] for r in res if r[0] == "ERROR"))
    print(f"\n{len(doc['shots'])} shots, {total:.1f} s planned: {ne} errors in {len(bad_shots)} shots/sheets, {nw} warnings")
    if a.json: Path(a.json).write_text(json.dumps({"errors": ne, "warnings": nw, "items": res, "planned_s": total}, indent=1))
    if a.write_qc and not ne:
        for s in doc["shots"]: s["qc"] = qc_spec(doc, s)
        doc.pop("_keyframes", None); doc.pop("_lora_registry", None); p.write_text(json.dumps(doc, indent=1, ensure_ascii=False)); print("qc specs written")
    sys.exit(1 if ne else 0)

if __name__ == "__main__":
    main()
