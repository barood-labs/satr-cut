"""Series bible to a lint-clean shot plan.

The model may suggest the premise, the emotional beat, and the lines. The shot
skeleton is constrained: a hook in the first 3 seconds, picture cuts of 1.5–2.5
seconds, an uncompleted-action cliffhanger, and one emotional beat. An auto-fix
loop rebuilds any shot that lint still rejects.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from satrcut.errors import SatrcutError
from satrcut.lint import lint_plan
from satrcut.llm import SYSTEM_WRITE, MockLLM, get_llm, parse_json_object
from satrcut.plan import generate_plan
from satrcut.project import Series, episode_plan_path, episode_yaml_path
from satrcut.speech import synthesize_plan
from satrcut import style
from satrcut.util import atomic_write_json

BEATS = ("rupture", "confession", "threat", "reversal", "longing")
SAFE_CAMERA = "Vertical close-up from the collarbones up. The camera creeps in slowly."
TROPES = {
    "revenge": {
        "emotional_beat": "rupture",
        "stakes_line": "You took the company.",
        "last_line": "Then I take the night.",
        "paywall_line": "Open the door. I dare you.",
        "time_card": "Three Nights Earlier",
        "title": "What was taken",
    },
    "billionaire": {
        "emotional_beat": "reversal",
        "stakes_line": "So this is who you are.",
        "last_line": "The name on the glass is mine.",
        "paywall_line": "Say my name.",
        "time_card": "The Night of the Gala",
        "title": "The name on the glass",
    },
    "contract": {
        "emotional_beat": "confession",
        "stakes_line": "Sign it, or walk away.",
        "last_line": "I already signed.",
        "paywall_line": "Clause seven. Read it.",
        "time_card": "The Morning of the Terms",
        "title": "The terms",
    },
    "second-chance": {
        "emotional_beat": "longing",
        "stakes_line": "I came back.",
        "last_line": "Then say it again.",
        "paywall_line": "Stay. Just stay.",
        "time_card": "One Year Later",
        "title": "Again",
    },
    "werewolf": {
        "emotional_beat": "threat",
        "stakes_line": "Your eyes changed.",
        "last_line": "Run, or stand with me.",
        "paywall_line": "The moon is up.",
        "time_card": "The Night of the Turning",
        "title": "Under the moon",
    },
}


def paywall_episode(number: int) -> bool:
    """A paywall cliffhanger every 6 episodes, inside the 5–8 episode band."""
    return number > 0 and number % 6 == 0


def trope_brief(name: str, number: int) -> dict[str, Any]:
    key = (name or "revenge").lower().replace("_", "-")
    if key in ("hidden-identity", "hidden-identity-billionaire"):
        key = "billionaire"
    if key in ("contract-marriage", "marriage"):
        key = "contract"
    if key in ("second_chance", "secondchance"):
        key = "second-chance"
    if key in ("fantasy", "wolf"):
        key = "werewolf"
    if key not in TROPES:
        known = ", ".join(TROPES)
        raise SatrcutError(f"unknown story template {name!r} ({known})")
    brief = dict(TROPES[key])
    brief["trope"] = key
    brief["paywall"] = paywall_episode(number)
    if brief["paywall"]:
        brief["last_line"] = brief["paywall_line"]
        brief["emotional_beat"] = "threat"
    return brief


def _copy_series(series: Series) -> Series:
    wardrobe = {place: dict(locked) for place, locked in series.wardrobe.items()}
    return replace(series, wardrobe=wardrobe, places=dict(series.places), characters=dict(series.characters))


def _ids(series: Series) -> tuple[str, str, str]:
    keys = list(series.characters)
    if not keys:
        raise SatrcutError("Add at least one character to series.yaml before satrcut write.")
    lead = keys[0]
    second = keys[1] if len(keys) > 1 else keys[0]
    third = keys[2] if len(keys) > 2 else second
    return lead, second, third


def _places(series: Series) -> tuple[str, str]:
    from satrcut.project import Place

    if not series.places:
        series.places["room"] = Place(id="room", desc="a dim interior, practical light, unreadable bokeh")
    keys = list(series.places)
    return keys[0], keys[1] if len(keys) > 1 else keys[0]


def _lock(series: Series, place: str, people: list[str]) -> None:
    locked = dict(series.wardrobe.get(place) or {})
    for person in people:
        character = series.characters[person]
        locked.setdefault(person, character.look or "the same wardrobe as the reference sheet")
    series.wardrobe[place] = locked


def _name(series: Series, person: str) -> str:
    return series.characters[person].name


def episode_sheet(series: Series, number: int, brief: dict[str, Any]) -> dict[str, Any]:
    """A beat sheet in the episode yaml shape. Frame counts are 4n+1 and short."""
    lead, second, third = _ids(series)
    place_a, place_b = _places(series)
    _lock(series, place_a, [lead, second])
    _lock(series, place_b, [third])
    scene_a = f"EXT. {place_a.replace('-', ' ').upper()} - NIGHT"
    scene_b = f"INT. {place_b.replace('-', ' ').upper()} - NIGHT"
    na, nb, nc = _name(series, lead), _name(series, second), _name(series, third)
    stakes = str(brief.get("stakes_line") or "Don't answer that.")
    last = str(brief.get("last_line") or "You were not supposed to be here.")
    beat = str(brief.get("emotional_beat") or BEATS[(number - 1) % len(BEATS)])
    if beat not in BEATS:
        beat = BEATS[(number - 1) % len(BEATS)]

    def face(shot_id: str, who: str, shot_type: str, frames: int, grade: str, **extra: Any) -> dict[str, Any]:
        row = {
            "id": shot_id,
            "scene": scene_a if who != third or shot_id.startswith("5-") is False else scene_a,
            "place": place_a,
            "shot_type": shot_type,
            "kind": "character",
            "frames": frames,
            "in_frame": [who],
            "faces_visible": [who],
            "grade": grade,
            "anchor": "keyed",
            "line_mode": "vo",
            "camera": f"Vertical {shot_type} of the face, from the collarbones up. The camera creeps in slowly.",
            "action": f"{_name(series, who)} stays in frame, lips sealed, eyes steady, catchlights bright, a slow blink.",
            "motion": "blink; catchlights; camera creeps in",
        }
        row.update(extra)
        return row

    shots: list[dict[str, Any]] = [
        face("1-hook", second, "ecu", 45, "desire", beat="hook", scene=scene_a, place=place_a),
    ]
    clock = 45 / 16 + 1.2
    index = 0
    who_cycle = (lead, second, lead, second)
    while clock < 68:
        who = who_cycle[index % len(who_cycle)]
        shot_id = f"b{index:02d}"
        if index % 8 == 7:
            shots.append(
                {
                    "id": shot_id,
                    "scene": scene_a,
                    "place": place_a,
                    "shot_type": "insert",
                    "kind": "insert",
                    "frames": 33,
                    "in_frame": [],
                    "faces_visible": [],
                    "grade": "danger",
                    "anchor": "keyed",
                    "line_mode": "insert",
                    "camera": "Vertical macro of a dark phone face-down on wet ground. The camera creeps in slowly.",
                    "action": "The dark phone screen wakes with a bright glow that blooms across the wet ground.",
                    "motion": "glow blooms; camera creeps in",
                }
            )
        else:
            extra: dict[str, Any] = {"scene": scene_a, "place": place_a}
            if index == 3:
                extra.update(line_mode="listener", speaker=f"{na} (O.S.)", line=stakes, subtitle=stakes)
            shots.append(face(shot_id, who, "cu" if index % 3 else "ecu", 33, "danger", **extra))
        clock += 33 / 16
        index += 1
    shots.append(
        {
            "id": "end-edge",
            "scene": scene_b,
            "place": place_b,
            "shot_type": "insert",
            "kind": "insert",
            "frames": 33,
            "in_frame": [],
            "faces_visible": [],
            "grade": "danger",
            "anchor": "keyed",
            "line_mode": "vo",
            "camera": "Vertical close shot of a painted metal panel. The camera creeps in slowly.",
            "action": "A stripe of warm light widens along the edge of the painted metal panel, dust turning in the beam.",
            "motion": "light stripe widens; camera creeps in",
        }
    )
    shots.append(
        face(
            "end-face",
            third,
            "cu",
            37,
            "danger",
            scene=scene_b,
            place=place_b,
            line_mode="listener",
            speaker=f"{na} (O.S.)",
            line=last,
            subtitle=last,
            end_on="line_end",
            beat="cliffhanger",
        )
    )
    # One character: the off-screen speaker must not be the face in frame.
    if lead == second:
        for shot in shots:
            if shot.get("speaker", "").startswith(na):
                shot["speaker"] = "Narrator (O.S.)"
    return {
        "episode": number,
        "title": str(brief.get("title") or beat[:1].upper() + beat[1:]),
        "trope": brief.get("trope") or "",
        "paywall": bool(brief.get("paywall")),
        "ending": "reveal" if brief.get("paywall") else "uncompleted_action",
        "emotional_beat": beat,
        "time_card": str(brief.get("time_card") or "Ten Minutes Earlier"),
        "stakes_line": stakes,
        "card_duration_s": 1.2,
        "seeds": [611, 733, 857, 919],
        "scenes": [
            {"id": scene_a, "place": place_a},
            {"id": scene_b, "place": place_b},
        ],
        "shots": shots,
    }


def season_arc(series: Series, episodes: int, premise: str, llm: Any | None = None, trope: str = "revenge") -> dict[str, Any]:
    model = llm or MockLLM()
    rows = []
    for number in range(1, episodes + 1):
        text = model.complete(
            SYSTEM_WRITE,
            f"episode: {number}\npremise: {premise}\ntrope: {trope}\nseries: {series.name}",
        )
        try:
            brief = parse_json_object(text)
        except (SatrcutError, ValueError):
            brief = {}
        brief.update(trope_brief(trope, number))
        brief["episode"] = number
        brief["cliffhanger"] = "uncompleted_action" if not brief.get("paywall") else "reveal"
        brief["premise"] = premise
        rows.append(brief)
    return {
        "series": series.name,
        "premise": premise,
        "trope": trope,
        "style": "dramabox",
        "provider": getattr(model, "name", "mock"),
        "episodes": rows,
    }


def stamp_dramabox(plan: dict[str, Any]) -> dict[str, Any]:
    """Mark the midpoint reversal and hang a next-episode stinger on the end."""
    cuts = list(plan.get("cuts") or [])
    if not cuts:
        return plan
    picture = [cut for cut in cuts if not str(cut.get("source") or "").startswith("card:")]
    if picture:
        runtime = float(cuts[-1]["at"]) + float(cuts[-1]["dur"])
        mid = runtime / 2
        target = min(picture, key=lambda cut: abs((float(cut["at"]) + float(cut["dur"]) / 2) - mid))
        if target.get("beat") != "hook":
            target["beat"] = "reversal"
            shot_id = target.get("shot_id")
            for shot in plan.get("shots") or []:
                if shot.get("id") == shot_id:
                    shot["beat"] = "reversal"
    end = cuts[-1]
    if end.get("source") != "card:next":
        cuts.append(
            {
                "n": int(end.get("n") or len(cuts)) + 1,
                "source": "card:next",
                "dur": 1.8,
                "at": round(float(end["at"]) + float(end["dur"]), 4),
                "framing": "CARD",
                "grade": "neutral",
                "text": "Next episode",
                "faces_visible": [],
                "push": None,
                "beat": "stinger",
            }
        )
    plan["cuts"] = cuts
    plan["runtime_s"] = round(float(cuts[-1]["at"]) + float(cuts[-1]["dur"]), 4)
    plan["target_runtime_s"] = plan["runtime_s"]
    plan["style"] = "dramabox"
    plan["next_episode"] = True
    return plan


def _rebuild_prompt(shot: dict[str, Any]) -> None:
    kind = shot.get("kind") if shot.get("kind") in ("insert", "aerial") else "character"
    shot["prompt"] = style.prompt(str(kind), str(shot.get("camera") or SAFE_CAMERA), str(shot.get("action") or ""), str(shot.get("grade") or "neutral"))
    shot["negative"] = style.negative(str(kind))


def autofix(plan: dict[str, Any], project: Path, *, rounds: int = 6) -> tuple[dict[str, Any], int]:
    """Rebuild shots that lint rejects. Returns the plan and how many passes ran."""
    used = 0
    while used < rounds:
        report = lint_plan(plan, project)
        if not report.errors:
            return plan, used
        used += 1
        by_shot: dict[str, list[str]] = {}
        plan_rules = []
        for item in report.errors:
            if item.shot_id in {shot.get("id") for shot in plan.get("shots") or []}:
                by_shot.setdefault(item.shot_id, []).append(item.rule)
            else:
                plan_rules.append(item.rule)
        for shot in plan.get("shots") or []:
            rules = " ".join(by_shot.get(shot["id"], []))
            if not rules and not plan_rules:
                continue
            if rules:
                who = (shot.get("faces_visible") or shot.get("in_frame") or ["the subject"])[0]
                label = str(who).split(" (")[0]
                label = label[:1].upper() + label[1:]
                shot["camera"] = SAFE_CAMERA
                shot["action"] = f"{label} stays in frame, lips sealed, eyes steady, catchlights bright, a slow blink."
                shot["anchor"] = shot.get("anchor") or "keyed"
                shot["start_frame"] = shot.get("start_frame") or f"keyframes/{shot['id']}_start.png"
                shot["end_frame"] = shot.get("end_frame") or f"keyframes/{shot['id']}_end.png"
                if "L04" in rules:
                    speaker = str(shot.get("speaker") or "").split(" (")[0].lower()
                    shot["faces_visible"] = [name for name in shot.get("faces_visible") or [] if str(name).lower() != speaker]
                    shot["line_mode"] = "listener" if shot["faces_visible"] else "vo"
                if "L03" in rules:
                    shot["faces_visible"] = (shot.get("faces_visible") or shot.get("in_frame") or [])[:1]
                    shot["shot_type"] = "cu"
                _rebuild_prompt(shot)
        if any(rule.startswith("L21") for rule in plan_rules):
            if plan.get("cuts"):
                plan["cuts"][0]["beat"] = "hook"
                plan["cuts"][0]["dur"] = 2.8125
            plan["stakes_at_s"] = min(float(plan.get("stakes_at_s") or 12), 20)
        if any(rule.startswith("L20") for rule in plan_rules):
            plan["ending"] = "uncompleted_action"
            if plan.get("cuts"):
                plan["cuts"][-1]["end_on"] = "line_end"
        if any(rule.startswith("L17") for rule in plan_rules):
            for cut in plan.get("cuts") or []:
                if str(cut.get("framing") or "").upper() in ("ESTABLISHING", "OTS", "WIDE"):
                    cut["framing"] = "CU"
        if any(rule.startswith("L16") for rule in plan_rules):
            for cut in plan.get("cuts") or []:
                if cut.get("source") != "card:time" and float(cut.get("dur") or 0) > 3:
                    cut["dur"] = 2.5
    return plan, used


def write_episode(
    series: Series,
    episode: int = 1,
    *,
    premise: str = "A secret told in close-ups.",
    llm: Any | None = None,
    episodes: int = 1,
    trope: str = "revenge",
) -> dict[str, Any]:
    series = _copy_series(series)
    model = llm or get_llm("mock")
    count = max(episodes, episode)
    arc = season_arc(series, count, premise, model, trope=trope)
    (series.root / "season.json").parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(series.root / "season.json", arc)
    written = []
    for row in arc["episodes"]:
        number = int(row["episode"])
        sheet = episode_sheet(series, number, row)
        path = episode_yaml_path(series.root, number)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(sheet, sort_keys=False, allow_unicode=True), encoding="utf-8")
        plan = generate_plan(series, sheet)
        plan["emotional_beat"] = sheet["emotional_beat"]
        plan["premise"] = premise
        plan["trope"] = sheet.get("trope") or trope
        plan["paywall"] = bool(sheet.get("paywall"))
        plan["title"] = sheet.get("title")
        plan["style"] = "dramabox"
        plan["target_runtime_s"] = plan["runtime_s"]
        plan["plan"] = "satrcut-v0.2"
        plan, passes = autofix(plan, series.root)
        plan = stamp_dramabox(plan)
        plan["autofix_passes"] = passes
        report = lint_plan(plan, series.root)
        if report.errors:
            raise SatrcutError("write could not reach a clean lint:\n" + report.render())
        atomic_write_json(episode_plan_path(series.root, number), plan)
        synthesize_plan(series.root, plan)
        written.append(plan)
    chosen = next(item for item in written if int(item["episode"]) == episode)
    return chosen
