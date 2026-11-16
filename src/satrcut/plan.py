"""Expand a series bible and an episode beat sheet into the shot-plan contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from satrcut import style
from satrcut.lint import qc_spec
from satrcut.project import Series, episode_plan_path, load_episode_yaml
from satrcut.util import atomic_write_json

PRESETS: dict[str, dict[str, Any]] = {
    "hybrid8": {
        "steps": 8,
        "split": 4,
        "shift": 5.0,
        "cfg": 1.0,
        "lora": 1.0,
        "sampler": "euler",
        "scheduler": "simple",
    }
}
LADDER = ["hybrid8"]


def _scene_table(series: Series, episode: dict[str, Any]) -> dict[str, Any]:
    scenes: dict[str, Any] = {}
    for scene in episode.get("scenes") or []:
        place = str(scene.get("place") or "")
        scenes[str(scene["id"])] = {
            "place": place,
            "wardrobe": dict(series.wardrobe.get(place) or {}),
            "wardrobe_change_shown": bool(scene.get("wardrobe_change_shown")),
        }
    return scenes


def _shot(series: Series, episode: dict[str, Any], raw: dict[str, Any], scenes: dict[str, Any]) -> dict[str, Any]:
    shot_id = str(raw["id"])
    kind = str(raw.get("kind") or "character")
    frames = int(raw["frames"])
    fps = int(raw.get("fps") or series.render.fps)
    place_id = str(raw.get("place") or (scenes.get(raw.get("scene") or "") or {}).get("place") or "")
    place = series.places.get(place_id)
    extra = f"Place: {place.desc}." if place and place.desc else ""
    camera = str(raw.get("camera") or style.CAMERA_V5)
    action = str(raw.get("action") or "")
    grade = str(raw.get("grade") or "neutral")
    shot: dict[str, Any] = {
        "id": shot_id,
        "scene": raw.get("scene"),
        "place": place_id,
        "method": "a14b",
        "kind": kind,
        "shot_type": raw.get("shot_type") or "cu",
        "frames": frames,
        "fps": fps,
        "duration_s": round(frames / fps, 4),
        "width": series.render.width,
        "height": series.render.height,
        "in_frame": list(raw.get("in_frame") or []),
        "faces_visible": list(raw.get("faces_visible") or []),
        "grade": grade,
        "anchor": raw.get("anchor") or "keyed",
        "camera": camera,
        "action": action,
        "motion": raw.get("motion") or "",
        "line_mode": raw.get("line_mode") or "vo",
        "start_frame": f"keyframes/{shot_id}_start.png",
        "end_frame": f"keyframes/{shot_id}_end.png",
        "seeds": list(raw.get("seeds") or episode.get("seeds") or [611, 733, 857, 919]),
        "prompt": style.prompt(kind if kind in ("insert", "aerial") else "character", camera, action, grade, extra),
        "negative": style.negative(kind, kiss=bool(raw.get("kiss"))),
    }
    for key in (
        "speaker",
        "line",
        "subtitle",
        "beat",
        "end_on",
        "allow_hands",
        "allow_short",
        "mouth_hidden",
        "kiss",
        "photo_faces",
        "sfx",
        "overlay",
    ):
        if key in raw and raw[key] not in (None, ""):
            shot[key] = raw[key]
    if shot.get("line") or shot.get("subtitle"):
        shot["line_audio"] = raw.get("line_audio") or f"audio/lines/{shot_id}.wav"
        shot["subtitle"] = shot.get("subtitle") or shot.get("line")
    return shot


def _cuts(episode: dict[str, Any], shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cuts: list[dict[str, Any]] = []
    clock = 0.0
    number = 0
    seen: set[str] = set()
    time_card = episode.get("time_card")
    card_dur = float(episode.get("card_duration_s") or 1.2)

    def add_shot(shot: dict[str, Any]) -> None:
        nonlocal clock, number
        number += 1
        duration = float(shot["duration_s"])
        framing = "CARD" if shot.get("shot_type") == "card" else str(shot.get("shot_type") or "cu").upper()
        cut: dict[str, Any] = {
            "n": number,
            "source": f"render:{shot['id']}",
            "shot_id": shot["id"],
            "dur": round(duration, 4),
            "at": round(clock, 4),
            "framing": framing,
            "grade": shot.get("grade") or "neutral",
            "faces_visible": list(shot.get("faces_visible") or []),
            "push": [1.0, 1.05],
            "piece": [0.0, round(duration, 4)],
            "beat": shot.get("beat"),
            "line_mode": shot.get("line_mode"),
        }
        if shot.get("line"):
            line = {
                "at": 0.2,
                "speaker": shot.get("speaker") or "",
                "text": shot["line"],
                "subtitle": shot.get("subtitle") or shot["line"],
                "file": shot.get("line_audio"),
                "mode": shot.get("line_mode"),
            }
            cut["lines"] = [line]
            cut["subs"] = [{"at": 0.2, "dur": max(0.6, duration - 0.45), "text": shot.get("subtitle") or shot["line"]}]
        if shot.get("sfx"):
            cut["sfx"] = shot["sfx"]
        if shot.get("end_on"):
            cut["end_on"] = shot["end_on"]
        for name in shot.get("faces_visible") or []:
            if name not in seen:
                seen.add(name)
                display = name[:1].upper() + name[1:]
                cut["name"] = [display, ""]
                break
        cuts.append(cut)
        clock += duration

    for index, shot in enumerate(shots):
        add_shot(shot)
        if index == 0 and time_card:
            number += 1
            cuts.append(
                {
                    "n": number,
                    "source": "card:time",
                    "dur": card_dur,
                    "at": round(clock, 4),
                    "framing": "CARD",
                    "grade": "neutral",
                    "text": time_card,
                    "faces_visible": [],
                    "push": None,
                }
            )
            clock += card_dur
    return cuts


def _stakes_at(cuts: list[dict[str, Any]], line: str | None) -> float | None:
    if not line:
        for cut in cuts:
            for entry in cut.get("lines") or []:
                return round(float(cut["at"]) + float(entry.get("at") or 0), 4)
        return None
    for cut in cuts:
        for entry in cut.get("lines") or []:
            if entry.get("text") == line or entry.get("subtitle") == line:
                return round(float(cut["at"]) + float(entry.get("at") or 0), 4)
    return None


def generate_plan(series: Series, episode: int | dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(episode, dict):
        raw = episode
        number = int(raw.get("episode") or 1)
    else:
        number = int(episode or 1)
        raw = load_episode_yaml(series.root, number)
    scenes = _scene_table(series, raw)
    shots = [_shot(series, raw, item, scenes) for item in raw.get("shots") or []]
    for shot in shots:
        shot["qc"] = qc_spec({"characters": _characters(series)}, shot)
    cuts = list(raw["cuts"]) if raw.get("cuts") else _cuts(raw, shots)
    stakes = raw.get("stakes_at_s")
    if stakes is None:
        stakes = _stakes_at(cuts, raw.get("stakes_line"))
    characters = _characters(series)
    plan = {
        "project": series.name,
        "episode": number,
        "title": raw.get("title") or f"Episode {number}",
        "plan": "satrcut-v0.2",
        "emotional_beat": raw.get("emotional_beat") or "",
        "lang": raw.get("lang") or series.raw.get("lang") or "en",
        "style": series.style,
        "aspect": series.aspect,
        "target_runtime_s": series.target_duration_s,
        "runtime_s": round(float(cuts[-1]["at"]) + float(cuts[-1]["dur"]), 4) if cuts else 0,
        "n_cuts": len(cuts),
        "hook_s": series.hook_s,
        "ending": raw.get("ending") or (series.endings[0] if series.endings else "uncompleted_action"),
        "stakes_at_s": stakes,
        "stakes_line": raw.get("stakes_line"),
        "time_card": raw.get("time_card"),
        "forbidden_names": list(series.forbidden_names),
        "lipsync_approved": bool(raw.get("lipsync_approved")),
        "characters": characters,
        "places": {key: {"desc": place.desc, "sheet": place.sheet} for key, place in series.places.items()},
        "wardrobe_lock": {key: dict(value) for key, value in series.wardrobe.items()},
        "scenes": scenes,
        "providers": dict(series.providers),
        "music": series.music,
        "render": {"width": series.render.width, "height": series.render.height, "fps": series.render.fps},
        "delivery": {"width": series.delivery.width, "height": series.delivery.height, "fps": series.delivery.fps},
        "presets": PRESETS,
        "ladder": list(LADDER),
        "default_preset": LADDER[0],
        "film_look": style.BOIL_V5,
        "shots": shots,
        "cuts": cuts,
        "root": ".",
    }
    return plan


def _characters(series: Series) -> dict[str, Any]:
    return {
        key: {
            "look": character.look,
            "unique_detail": character.unique_detail,
            "voice": character.voice,
            "sheet": character.sheet,
            "display": character.name,
        }
        for key, character in series.characters.items()
    }


def write_plan(series: Series, episode: int = 1) -> Path:
    plan = generate_plan(series, episode)
    path = episode_plan_path(series.root, episode)
    atomic_write_json(path, plan)
    from satrcut.speech import synthesize_plan

    synthesize_plan(series.root, plan)
    return path
