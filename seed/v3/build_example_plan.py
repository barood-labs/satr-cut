#!/usr/bin/env python3
"""Build a generic 10-shot, ~20 s vertical demo plan and a job.json for runner_v3.

This replaces the series-specific episode builders that were removed from the
public seed. It shows the shot schema, lint-friendly prompting (describe what
IS in frame; no hands, no negations), locked start/end anchors, and the job
folder layout the crash-resume runner expects.

Characters are placeholders (Alex, Jordan, Sam). The plot is a throwaway hook:
a rooftop almost-confession, cut on an uncompleted action.

  python3 v3/build_example_plan.py --out examples/demo_ep01_plan.json --jobs examples/demo_job
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import style as S

PUSH = S.CAMERA_V5
DEMO_CHARS = {
    "Alex": {"look": S.WARDROBE["Alex"], "unique_detail": "a thin scar at the left eyebrow", "sheet": "assets/alex_sheet.png"},
    "Jordan": {"look": S.WARDROBE["Jordan"], "unique_detail": "a gold hoop in the left ear", "sheet": "assets/jordan_sheet.png"},
    "Sam": {"look": S.WARDROBE["Sam"], "unique_detail": "a sharp chin-length bob", "sheet": "assets/sam_sheet.png"},
}
PLACES = {
    "rooftop": {"desc": "night rooftop, wet concrete, neon signage out of focus and unreadable, city bokeh"},
    "stairwell": {"desc": "narrow concrete stairwell, one bare bulb, painted metal rail"},
}

# id, frames (4n+1 for Wan), kind, scene, in_frame, faces_visible, shot_type, grade, camera, action, motion, extra
NEW = [
    ("1-hook", 49, "character", "EXT. ROOFTOP - NIGHT", ["Alex", "Jordan"], ["Jordan"], "ecu", "desire",
     "Vertical extreme close-up of her eyes, " + PUSH,
     "Jordan: her eyes stay locked on a figure just off-camera, catchlights bright, lips sealed, a slow blink, neon bokeh behind.",
     "slow blink; catchlights; camera creeps in", {"beat": "hook"}),
    ("1-time", 0, "card", "TITLE", [], [], "card", "neutral",
     "", "Time card.", "", {"text": "Ten Minutes Earlier", "card": True}),
    ("2-est", 33, "aerial", "EXT. ROOFTOP - NIGHT", [], [], "establishing", "danger",
     "Vertical aerial of wet rooftops and a single lit doorway, " + PUSH,
     "The camera glides in toward the lit doorway along the wet roof ridge, city lights staying still in the distance.",
     "slow glide toward the doorway", {"allow_short": "1-2 s establishing insert"}),
    ("2-ecu", 41, "character", "EXT. ROOFTOP - NIGHT", ["Alex"], ["Alex"], "ecu", "danger",
     "Vertical extreme close-up of his eyes, " + PUSH,
     "Alex: his tired eyes snap wider, brows rising, a sharp breath, lips sealed, cold blue neon on the skin.",
     "eyes widen; brows rise; breath; camera creeps in", {}),
    ("2-react", 33, "character", "EXT. ROOFTOP - NIGHT", ["Jordan"], ["Jordan"], "cu", "desire",
     "Vertical close-up in side profile, " + PUSH,
     "Jordan turns from profile toward camera, eyes glistening, lips parted with a held breath, amber light on her jaw.",
     "head turn profile to three-quarter; held breath", {"allow_short": "1 s reaction", "line_mode": "listener"}),
    ("3-insert", 41, "insert", "EXT. ROOFTOP - NIGHT", [], [], "insert", "danger",
     "Vertical macro of a black smartphone face-down on wet concrete, " + PUSH,
     "The dark phone screen wakes with a bright incoming-call glow that blooms across the wet concrete.",
     "screen wakes; glow blooms; camera creeps in", {"overlay": "incoming call"}),
    ("3-os", 49, "character", "EXT. ROOFTOP - NIGHT", ["Jordan"], ["Jordan"], "cu", "danger",
     "Vertical close-up of the listener, " + PUSH,
     "Jordan listens, eyes widening, lips sealed, the neon behind her staying still.",
     "eyes widen; lips sealed; camera creeps in",
     {"line_mode": "listener", "speaker": "Alex (O.S.)",
      "line": "Don't answer that.", "subtitle": "Don't answer that.",
      "line_audio": "audio/lines/3-os.wav"}),
    ("4-ots", 49, "character", "EXT. ROOFTOP - NIGHT", ["Alex", "Jordan"], ["Jordan"], "ots", "desire",
     "Vertical over-the-shoulder: the back of Alex's head soft in the foreground, Jordan sharp, " + PUSH,
     "Jordan meets his gaze, a breath, lips sealed, faces kept a clear gap apart, amber light on her jaw.",
     "held gaze; breath; camera creeps in", {"line_mode": "os", "mouth_hidden": True}),
    ("5-door", 41, "insert", "INT. STAIRWELL - NIGHT", [], [], "insert", "danger",
     "Vertical close shot of a painted metal door, slightly ajar, " + PUSH,
     "A stripe of warm light widens slowly as the door eases farther ajar, dust turning in the beam.",
     "light stripe widens; door eases ajar", {"end_on": "uncompleted_action", "line_mode": "vo", "speaker": "Sam (O.S.)",
      "line": "Wait.", "subtitle": "Wait.", "line_audio": "audio/lines/5-door.wav"}),
    ("5-end", 41, "character", "INT. STAIRWELL - NIGHT", ["Sam"], ["Sam"], "cu", "danger",
     "Vertical close-up just inside the doorway, " + PUSH,
     "Sam stands in the warm light, eyes on a figure ahead, lips sealed, the door still moving at the edge of frame.",
     "held look; door still moving; camera creeps in",
     {"line_mode": "listener", "speaker": "Alex (O.S.)", "line": "You were not supposed to open it.",
      "subtitle": "You were not supposed to open it.",
      "line_audio": "audio/lines/5-end.wav", "end_on": "confession"}),
]

PRESETS = {
    "hybrid8": {"steps": 8, "cfg": 1.0, "sampler": "euler", "scheduler": "simple", "shift": 8.0, "width": 480, "height": 848, "fps": 16},
}
LADDER = ["hybrid8"]
SEEDS = [611, 733, 857, 919]


def shot_from_row(row):
    sid, fr, kind, scene, inf, fv, st, grade, cam, act, mot, ex = row
    if ex.get("card"):
        return None
    s = {
        "id": sid, "scene": scene, "method": "a14b", "frames": fr, "fps": 16,
        "duration_s": round(fr / 16, 2), "kind": kind, "in_frame": inf,
        "faces_visible": fv, "shot_type": st, "framing": st, "grade": grade,
        "camera": cam, "action": act, "motion": mot, "anchor": "keyed",
        "start_frame": f"keyframes/{sid}_start.png",
        "end_frame": f"keyframes/{sid}_end.png",
        "prompt": S.prompt(kind if kind in ("insert", "aerial") else "character", cam, act, grade),
        "negative": S.NEG_V5_NOHANDS if kind != "character" else S.NEG_V5,
        "seeds": list(SEEDS), "status": "todo",
        "line_mode": ex.get("line_mode", "vo" if not ex.get("line") else ex.get("line_mode", "listener")),
    }
    for k in ("allow_hands", "allow_short", "overlay", "line", "subtitle", "speaker", "mouth_hidden", "end_on", "beat", "line_audio", "line_mode"):
        if k in ex:
            s[k] = ex[k]
    return s


def build(out: Path, jobs: Path):
    shots = []
    cuts = []
    t = 0.0
    n = 0
    for row in NEW:
        ex = row[-1]
        if ex.get("card"):
            n += 1
            dur = 1.2
            cuts.append({"n": n, "source": "card:time", "dur": dur, "at": round(t, 3),
                         "text": ex.get("text", "Earlier"), "grade": "neutral"})
            t += dur
            continue
        s = shot_from_row(row)
        shots.append(s)
        n += 1
        dur = s["duration_s"]
        cut = {"n": n, "source": f"v5:{s['id']}", "dur": dur, "at": round(t, 3),
               "framing": s["framing"], "grade": s["grade"], "faces_visible": s["faces_visible"]}
        if s.get("subtitle"):
            cut["subs"] = [{"at": 0.15, "dur": max(0.8, dur - 0.3), "text": s["subtitle"]}]
        if s.get("line"):
            cut["lines"] = [{"at": 0.15, "speaker": s.get("speaker", ""), "text": s["line"]}]
        if s.get("end_on"):
            cut["end_on"] = s["end_on"]
        if s.get("beat"):
            cut["beat"] = s["beat"]
        cuts.append(cut)
        t += dur
    total = round(t, 2)
    doc = {
        "project": "demo-hook",
        "episode": 1,
        "plan": "example-v1",
        "style": "docs/STYLE_RULES.md + v3/style.py",
        "target_runtime_s": 25,
        "runtime_s": total,
        "n_cuts": len(cuts),
        "hook": {"cut": 1, "what": "rooftop eyes in media res", "dur": cuts[0]["dur"]},
        "time_card": {"cut": 2, "text": "Ten Minutes Earlier", "font": "Alex Brush", "colour": "red"},
        "ending": "uncompleted_action",
        "characters": DEMO_CHARS,
        "places": PLACES,
        "wardrobe_lock": S.WARDROBE,
        "scenes": {
            "EXT. ROOFTOP - NIGHT": {"wardrobe": {"Alex": S.WARDROBE["Alex"], "Jordan": S.WARDROBE["Jordan"]}, "wardrobe_change_shown": False},
            "INT. STAIRWELL - NIGHT": {"wardrobe": {"Sam": S.WARDROBE["Sam"]}, "wardrobe_change_shown": False},
        },
        "cuts": cuts,
        "shots": shots,
        "presets": PRESETS,
        "ladder": LADDER,
        "film_look": S.BOIL_V5,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / "job.json").write_text(json.dumps({
        "shots": shots, "stages": ["wan"], "presets": PRESETS, "ladder": LADDER,
        "default_preset": "hybrid8", "max_tries": 2,
        "note": "demo job for the mock or ComfyUI provider; no start/end PNGs are shipped in the seed",
    }, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out}  cuts={len(cuts)}  runtime={total}s  renders={len(shots)}")
    print(f"wrote {jobs / 'job.json'}")
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE.parent / "examples" / "demo_ep01_plan.json")
    ap.add_argument("--jobs", type=Path, default=HERE.parent / "examples" / "demo_job")
    a = ap.parse_args()
    build(a.out, a.jobs)

if __name__ == "__main__":
    main()
