#!/usr/bin/env python3
"""shotlint v5 (DramaBox rules, Nov 14 12:08-12:22 MT). Wraps the v2 linter (own-tool/v2/shotlint.py, used read-only) on the
plan's render shots, then adds edit-level rules on the cut list:
  L03 kiss exemption: kisses are ALLOWED from ep1 (user 12:11) -> CONTACT / TWO_FACES errors are dropped for shots with a
      staged `kiss` block {angle: profile|ots, soft_face: <name>} (face-merge staging) - unstaged contact stays an ERROR.
  K01 KISS_STAGING  kiss shot: angle profile/ots, soft_face set, start+end pinned keyframes, kind=kiss gate, >= 3 tries
  K02 KISS_LENGTH   kiss cut 3-7 s; seduction beat 3.5-5.5 s; a reaction cut must follow the kiss
  L16 MAX_CUT       every cut <= 3.0 s (kiss/seduce exempt as above)
  L17 CU_RATIO      >= 70 % of picture cuts are CU/ECU
  L18 CAMERA_MOVE   every render prompt has a moving camera (creep/push/dolly/drift/handheld/drone); every cut has push-in
  L19 LATERAL_WALK  'walks across / left to right / past the camera' flagged (walk on the Z axis instead)
  L20 ENDING        plan.ending.type in uncompleted_action|demand|entrance|confession|surrender|interruption; last cut ends on a line
  L21 HOOK/STAKES   cut 1 is a 2.5-3.5 s hook, cut 2 a time card, the 'stakes' line lands <= 30 s
  L22 STYLE_WORDS   render prompts carry rim lighting + glossy + 85mm (or macro/aerial) + catchlights on faces
  L23 GRADE         warm grades only on desire/flashback beats; danger/grief cuts cool
  L24 SPEAKER       O.S./V.O. line on a cut whose faces_visible includes the speaker -> ERROR
  L25 NAMES         optional per-project forbidden name list (series rule, default empty)
usage: shotlint_v5.py PLAN.json [--json OUT]   exit 0 if no ERROR"""
import argparse, json, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "v2")); sys.path.insert(0, str(HERE))
import shotlint as v2
import style as S
MOVE = r"\b(creeps?|creeping|push(es)? in|dolly|drifts?|drifting|handheld|drone|glid\w+|rising|tilts?|follow\w*|jolts?|backs? away)\b"
LATERAL = r"\b(walks? across|crosses the frame|left to right|right to left|walks? past the camera|strides? across)\b"
def _forbidden_name_re(doc):
    names = doc.get("forbidden_names") or []
    if not names:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I)

def lint(doc):
    out = []; E = lambda s, r, m: out.append(("ERROR", s, r, m)); W = lambda s, r, m: out.append(("WARN", s, r, m))
    shots = doc.get("shots", []); by = {s["id"]: s for s in shots}
    base, _ = v2.lint({"episode": doc.get("episode"), "shots": shots}, doc.get("episode"), False, Path(doc.get("root") or "."))
    for lvl, sid, rule, msg in base:
        s = by.get(sid, {})
        if s.get("kiss") and rule in ("L03 CONTACT", "L03 TWO_FACES"):
            out.append(("INFO", sid, rule + " (kiss allowed)", "staged kiss: " + json.dumps(s["kiss"]))); continue
        if rule == "L12 SHORT" and s.get("allow_short"): continue
        if rule == "L15 RUNTIME": continue
        out.append((lvl, sid, rule, msg))
    for s in shots:
        sid = s["id"]; p = s.get("prompt", "")
        if s.get("kiss"):
            k = s["kiss"]
            if k.get("angle") not in ("profile", "ots", "three_quarter"): E(sid, "K01 KISS_STAGING", "kiss angle must be profile / ots / 3/4")
            if not k.get("soft_face"): E(sid, "K01 KISS_STAGING", "one face must be partly turned or soft (soft_face)")
            if not (s.get("start_frame") and s.get("end_frame")): E(sid, "K01 KISS_STAGING", "pinned start AND end keyframes required")
            if k.get("beat") == "kiss" and s.get("kind") != "kiss": E(sid, "K01 KISS_STAGING", "kiss render must use gate kind=kiss (merge/morph)")
            if k.get("beat") == "kiss" and len(s.get("presets", [])) < 3: W(sid, "K01 KISS_STAGING", "give the kiss >= 3 tries (presets list)")
        if not re.search(MOVE, s.get("camera", "") + " " + p, re.I): E(sid, "L18 CAMERA_MOVE", "render prompt has no camera move (DramaBox: slow push-in or drift on every shot)")
        if re.search(LATERAL, s.get("action", ""), re.I): W(sid, "L19 LATERAL_WALK", "lateral walk reads flat; stage on the Z axis (toward/away from camera)")
        need = ["rim", "glossy"] + (["85mm"] if s.get("v5_kind") != "aerial" else [])
        miss = [w for w in need if w not in p.lower()]
        if miss: E(sid, "L22 STYLE_WORDS", f"prompt lacks {miss} (style.BOIL_V5)")
        if s.get("faces_visible") and "catchlight" not in p.lower(): W(sid, "L22 STYLE_WORDS", "face shot without catchlights")
        if s.get("grade") == "desire" and not s.get("kiss") and "desire" not in json.dumps(s.get("beat", "")) and sid not in ("3-k2",):
            W(sid, "L23 GRADE", "warm desire grade outside a desire beat")
    cuts = doc.get("cuts", [])
    if not cuts: E("plan", "L16 MAX_CUT", "no cut list"); return out
    pic = [c for c in cuts if c["framing"] != "CARD"]
    for i, c in enumerate(cuts):
        cid = f"cut{c['n']:02d}"; b = c.get("beat")
        if b == "kiss":
            if not (3.0 <= c["dur"] <= 7.0): E(cid, "K02 KISS_LENGTH", f"kiss cut {c['dur']} s (3-7 s)")
            nxt = cuts[i + 1] if i + 1 < len(cuts) else None
            if not nxt or nxt["framing"] not in ("CU", "ECU"): E(cid, "K02 KISS_LENGTH", "kiss must be followed by a CU reaction")
        elif b == "seduce":
            if not (3.5 <= c["dur"] <= 5.5): W(cid, "K02 SEDUCE_LENGTH", f"seductive beat {c['dur']} s (~4.5 s)")
        elif c["dur"] > S.EDIT["max_cut_s"] + 1e-6: E(cid, "L16 MAX_CUT", f"{c['source']} {c['dur']} s > 3 s")
        if c["framing"] != "CARD" and not c.get("push"): E(cid, "L18 CAMERA_MOVE", "cut without push-in")
        if c["grade"] in ("desire", "flashback") and b not in ("hook", "seduce", "kiss") and c["source"] not in ("v5:3-k2",) and c["grade"] != "flashback":
            W(cid, "L23 GRADE", "warm grade outside desire")
        for l in c.get("lines", []):
            spk = l.get("speaker", "").split(" (")[0].title()
            if l.get("mode") in ("os", "vo") and any(spk.lower() == f.split(" (")[0].lower() for f in c.get("faces_visible", [])):
                E(cid, "L24 SPEAKER", f"{spk}'s O.S./V.O. line plays over {spk}'s own face")
            rx = _forbidden_name_re(doc)
            if rx and rx.search(l.get("subtitle", "")): E(cid, "L25 NAMES", "forbidden name in a line (project rule)")
    cu = sum(c["framing"] in ("CU", "ECU") for c in pic) / max(1, len(pic))
    (E if cu < S.EDIT["min_cu_ratio"] else lambda *a: out.append(("INFO",) + a))("plan", "L17 CU_RATIO", f"CU/ECU {cu:.0%} of {len(pic)} picture cuts (need >= 70 %)")
    if cuts[0].get("beat") != "hook" or not (2.5 <= cuts[0]["dur"] <= 3.5): E("plan", "L21 HOOK", "cut 1 must be a 2.5-3.5 s hook")
    if len(cuts) < 2 or cuts[1]["source"] != "card:time": W("plan", "L21 TIME_CARD", "hook should hard-cut to a time card")
    st = doc.get("stakes_at_s")
    if st is None or st > S.EDIT["stakes_by_s"]: E("plan", "L21 STAKES", f"stakes line at {st} s (<= 30 s)")
    en = doc.get("ending", {}); types = en.get("type", []); types = [types] if isinstance(types, str) else types
    if not types or not all(t in S.EDIT["endings"] for t in types): E("plan", "L20 ENDING", f"ending type {types} not in {S.EDIT['endings']}")
    if cuts[-1].get("end_on") != "line_end": W("plan", "L20 ENDING", "last cut should end on the word of the final line")
    for n in (doc.get("wardrobe_lock") or {}):
        rx = _forbidden_name_re(doc)
        if rx and rx.search(n): E("plan", "L25 NAMES", f"forbidden name {n} (project rule)")
    tot = cuts[-1]["at"] + cuts[-1]["dur"]
    if not (80 <= tot <= 100): W("plan", "L15 RUNTIME", f"{tot:.1f} s")
    if not (35 <= len(cuts) <= 45): W("plan", "L15 CUTS", f"{len(cuts)} cuts (35-45)")
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("plan"); ap.add_argument("--json"); a = ap.parse_args()
    doc = json.loads(Path(a.plan).read_text()); res = lint(doc)
    for lvl, sid, rule, msg in res: print(f"{lvl:5} {sid:8} {rule:28} {msg}")
    ne = sum(r[0] == "ERROR" for r in res); nw = sum(r[0] == "WARN" for r in res)
    tot = doc["cuts"][-1]["at"] + doc["cuts"][-1]["dur"] if doc.get("cuts") else 0
    print(f"\n{len(doc.get('cuts', []))} cuts, {tot:.1f} s, {len(doc.get('shots', []))} renders: {ne} errors, {nw} warnings")
    if a.json: Path(a.json).write_text(json.dumps({"errors": ne, "warnings": nw, "items": res}, indent=1))
    sys.exit(1 if ne else 0)
