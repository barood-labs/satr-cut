"""Shot-plan linter.

Rule ids and the regular expressions are the ones in seed/v2/shotlint.py and
seed/v3/shotlint_v5.py. A positive prompt is what Wan2.2 draws at cfg 1.0, so
naming a hand, a wheel, or a negation is an error before any GPU work.

L09 is tighter than the seed: a keyed or drift shot must name an end frame.
A pin anchor may omit it, because the end frame is the start frame.
Wardrobe changes compare the scene lock, not only the character sheet.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from satrcut import style as S

Level = Literal["ERROR", "WARN", "INFO"]

HAND = (
    r"\b(hands?|fingers?|fingertips?|palms?|wrists?|knuckles?|thumbs?|fists?|grips?|gripping|grabs?|grabbing|"
    r"clutch\w*|holds?|holding|clasp\w*|touch\w*|strok\w*|caress\w*|trac(e|es|ing)|rings?|watch|bracelet|bandana|"
    r"steering|wheel|gear ?shift\w*|stalk|lifts?|lifting|picks? up|sets? (down|the)|pulls?|pushes?|slides?|presses|"
    r"pressed|reaches|reaching|mugs?|cups?|phone to (his|her) ear|cover\w* (his|her) (face|mouth))\b"
)
CONTACT = (
    r"\b(kiss\w*|lips? (touch|almost|brush|hover|meet|graze)\w*|almost touching|nearly touching|"
    r"lean(s|ing)? (in|close|toward)|forehead|nose[- ]to[- ]nose|cheek|embrac\w*|hug\w*|"
    r"pulls? (him|her) (in|close)|close together|a breath from|whisper\w* in (his|her) ear|nuzzl\w*|face to face)\b"
)
NEGATION = r"\b(no|not|never|without|doesn'?t|does not|don'?t|isn'?t|nobody|out of frame|off[- ]screen)\b"
DOOR = (
    r"\b(door (opens|swings|creaks|is open|open\w*)|opens the door|swings (open|inward|outward)|walks? in|enters|"
    r"entering|steps? in|comes? in|appears? behind|crowd|passers?-?by|pedestrians?|bystanders?|"
    r"people in the background|someone)\b"
)
VEHICLE = (
    r"\b(truck|lorry|bus|traffic|cars?|vehicles?|trailer|ambulance)\b.{0,60}\b(distant|in the distance|far|"
    r"approach\w*|drives?|driving|speed\w*|across the lanes|jackknif\w*|brakes?|swerv\w*|skid\w*|races?|racing)\b"
)
WIPER = r"\bwipers?\b"
TEXT = r"\b(signs?|signage|text|labels?|patch(es)?|logos?|captions?|headlines?|reads? [\"']|screen shows|written|lettering|numbers? on)\b"
PHOTO = r"\b(photo(graph)?s?|picture|portrait|framed)\b"
CAMERA_MOVE = r"\b(push[- ]?in|pull[- ]?(out|back)|dolly|pans?|panning|zoom\w*|orbit\w*|tracking|crane|tilts?|handheld|whip)\b"
HAND_DETAIL = r"\b(ring|rings|watch|bracelet|wrist|hand|hands|fingers?|bandana)\b"
MOVE = r"\b(creeps?|creeping|push(es)? in|dolly|drifts?|drifting|handheld|drone|glid\w+|rising|tilts?|follow\w*|jolts?|backs? away)\b"
LATERAL = r"\b(walks? across|crosses the frame|left to right|right to left|walks? past the camera|strides? across)\b"
LISTENER_MOUTH = r"\b(lips (pressed|sealed|closed)|mouth (closed|shut)|jaw (set|clenched|tight))\b"


def find(pattern: str, text: str) -> list[str]:
    return sorted({match.group(0).lower() for match in re.finditer(pattern, text or "", re.I)})


@dataclass(frozen=True)
class Finding:
    level: Level
    shot_id: str
    rule: str
    message: str


@dataclass
class LintReport:
    findings: list[Finding]
    planned_s: float

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.level == "ERROR"]

    @property
    def warnings(self) -> list[Finding]:
        return [item for item in self.findings if item.level == "WARN"]

    def render(self) -> str:
        lines = [f"{item.level:5} {item.shot_id:12} {item.rule:22} {item.message}" for item in self.findings]
        shots = {item.shot_id for item in self.errors}
        lines.append(
            f"\n{self.planned_s:.1f} s planned: {len(self.errors)} errors in {len(shots)} shots, {len(self.warnings)} warnings"
        )
        return "\n".join(lines)


def _episode_number(doc: dict[str, Any], episode: int | None) -> int:
    raw = episode if episode is not None else doc.get("episode") or 0
    digits = re.sub(r"\D", "", str(raw))
    try:
        return int(digits or 0)
    except ValueError:
        return 0


def _prompt_text(shot: dict[str, Any]) -> str:
    parts = [str(shot.get(key) or "") for key in ("action", "camera", "setting", "motion")]
    return str(shot.get("prompt") or "") + " " + " ".join(parts)


def lint_shots(
    doc: dict[str, Any],
    *,
    episode: int | None = None,
    allow_lipsync: bool = False,
    root: Path | None = None,
) -> tuple[list[Finding], float]:
    """L01–L14 from seed/v2/shotlint.py, plus the tightened end-frame rule."""
    root = root or Path(doc.get("root") or ".")
    out: list[Finding] = []

    def add(level: Level, shot_id: str, rule: str, message: str) -> None:
        out.append(Finding(level, shot_id, rule, message))

    chars: dict[str, Any] = doc.get("characters") or {}
    scenes: dict[str, Any] = doc.get("scenes") or {}
    ep = _episode_number(doc, episode)
    for name, character in chars.items():
        blob = f"{character.get('unique_detail', '')} {character.get('look', '')}"
        bad = find(HAND_DETAIL, blob)
        if bad:
            add(
                "ERROR",
                f"char:{name}",
                "L11 HAND_DETAIL",
                f"character sheet names {bad}. Use a face, neck, or collar detail so the prompt does not draw hands.",
            )
    total = 0.0
    previous: dict[str, tuple[str, str]] = {}
    for shot in doc.get("shots") or []:
        shot_id = str(shot.get("id") or "?")
        prompt = _prompt_text(shot)
        method = shot.get("method", shot.get("model", ""))
        fps = float(shot.get("fps") or 16)
        frames = int(shot.get("frames") or 0)
        duration = float(shot.get("duration_s") or ((frames / fps) if fps else 0))
        total += duration
        in_frame = list(shot.get("in_frame") or [])
        faces = list(shot.get("faces_visible") if shot.get("faces_visible") is not None else in_frame)
        shot_type = shot.get("shot_type") or ""
        hands = find(HAND, prompt)
        if hands and not shot.get("allow_hands"):
            add(
                "ERROR",
                shot_id,
                "L01 HANDS",
                f"prompt names {hands}. Frame from the collarbones up, or set allow_hands with a reason (the gate returns REVIEW).",
            )
        elif hands:
            add(
                "WARN",
                shot_id,
                "L01 HANDS",
                f"deliberate hand insert ({shot.get('allow_hands')}): pin the frame, two hands max, and a person must look.",
            )
        negations = find(NEGATION, prompt)
        if negations and method in ("a14b", ""):
            add(
                "ERROR",
                shot_id,
                "L02 NEGATION",
                f"positive prompt contains {negations}. At cfg 1.0 the negative prompt is ignored and the negated noun is drawn. Say what IS in frame.",
            )
        contact = find(CONTACT, prompt)
        if contact:
            level: Level = "ERROR" if (ep and ep < 10) or len(faces) >= 2 else "WARN"
            add(
                level,
                shot_id,
                "L03 CONTACT",
                f"{contact}: faces that close the gap merge. Keep at least half a face width between faces, or cut to a single close-up.",
            )
        if len(faces) >= 2 and shot_type not in ("two_shot_wide", "ots"):
            add(
                "ERROR",
                shot_id,
                "L03 TWO_FACES",
                f"{len(faces)} visible faces in a close frame. Use ots, two_shot_wide with a 0.45 face-width gap, or two singles.",
            )
        has_line = bool(shot.get("line") or shot.get("subtitle") or shot.get("audio"))
        if has_line:
            mode = shot.get("line_mode")
            speaker = (shot.get("speaker") or "").split(" (")[0].strip()
            on_screen = any(speaker and str(name).split(" (")[0].lower() == speaker.lower() for name in faces)
            if not mode:
                add("ERROR", shot_id, "L04 LINE_MODE", "dialogue shot needs line_mode: os, listener, vo, insert, or lipsync_cu.")
            elif mode == "lipsync_cu" and not allow_lipsync:
                add(
                    "ERROR",
                    shot_id,
                    "L04 LIPSYNC_BANNED",
                    "on-screen lip-sync is off until a close-up fixture passes. Put the line on the listener, an OTS with the mouth hidden, or an insert.",
                )
            elif mode in ("os", "listener", "vo", "insert") and on_screen:
                add(
                    "ERROR",
                    shot_id,
                    "L04 SPEAKER_VISIBLE",
                    f"{speaker}'s line is off-screen but {speaker}'s face is in frame. Remove the speaker from faces_visible or move the line.",
                )
            line_audio = shot.get("line_audio") or shot.get("audio")
            if shot.get("subtitle") and not line_audio:
                add("ERROR", shot_id, "L04 LINE_AUDIO", "subtitle with no line_audio on this shot.")
            elif line_audio and not (root / str(line_audio)).exists() and not Path(str(line_audio)).exists():
                add("WARN", shot_id, "L04 LINE_AUDIO", f"line audio file not found yet: {line_audio}")
            if faces and mode in ("listener", "os") and not re.search(LISTENER_MOUTH, prompt, re.I):
                add(
                    "WARN",
                    shot_id,
                    "L04 LISTENER_MOUTH",
                    "listener shot should say 'lips sealed' or 'jaw set' in the positive prompt.",
                )
        doors = find(DOOR, prompt)
        if doors:
            add("ERROR", shot_id, "L05 DOOR_PEOPLE", f"{doors}: show the closed door and carry the entrance with sound plus the next shot.")
        if re.search(r"\bdoor\b", prompt, re.I) and not shot.get("anchor") and not shot.get("end_frame"):
            add("ERROR", shot_id, "L05 DOOR_ANCHOR", "a door in frame needs a pinned end keyframe so it cannot swing open.")
        vehicles = find(VEHICLE, prompt)
        if vehicles:
            add("ERROR", shot_id, "L06 VEHICLE_MOTION", f"{vehicles}: distant or moving vehicles melt. Use a pinned interior, plus sound.")
        if find(WIPER, prompt):
            add("ERROR", shot_id, "L06 WIPERS", "wipers smear across faces. Park them out of frame.")
        text_hits = find(TEXT, prompt)
        if text_hits:
            add("WARN", shot_id, "L07 TEXT", f"{text_hits}: generated glyphs come out as garbage. Burn text in assembly.")
        if find(PHOTO, prompt) and not shot.get("photo_faces"):
            add("WARN", shot_id, "L08 PHOTO_FACE", "a photo prop's face animates. Set photo_faces and pin start to end, or keep the photo face-down.")
        if method == "a14b":
            if not shot.get("start_frame"):
                add("ERROR", shot_id, "L09 START", "no start keyframe")
            anchor = shot.get("anchor")
            if not shot.get("end_frame") and anchor != "pin":
                add(
                    "ERROR",
                    shot_id,
                    "L09 END_ANCHOR",
                    "no end keyframe. Register one, or set anchor pin so the end frame is the start frame.",
                )
            registered = doc.get("_keyframes") or {}
            for role in ("start_frame", "end_frame"):
                frame = shot.get(role)
                if frame and registered and frame not in registered:
                    add("WARN", shot_id, "L09 UNREGISTERED", f"{role} {frame} is not in the keyframe manifest")
        scene = scenes.get(shot.get("scene") or "", {})
        scene_wardrobe = scene.get("wardrobe") or {}
        for name in in_frame:
            base = str(name).split(" (")[0]
            if scenes and name not in scene_wardrobe and base not in scene_wardrobe:
                add("ERROR", shot_id, "L10 WARDROBE", f"scene {shot.get('scene')!r} has no locked wardrobe for {name}.")
            look = scene_wardrobe.get(name) or scene_wardrobe.get(base) or (chars.get(name) or chars.get(base) or {}).get("look", "")
            prior = previous.get(base)
            if prior and prior[0] != look and not scene.get("wardrobe_change_shown") and prior[1] != shot.get("scene"):
                add(
                    "WARN",
                    shot_id,
                    "L10 WARDROBE_CHANGE",
                    f"{base} changes wardrobe from {prior[1]!r} without a shot that shows the change. Set wardrobe_change_shown after that shot.",
                )
            previous[base] = (str(look), str(shot.get("scene") or ""))
        if frames and frames % 4 != 1:
            add("ERROR", shot_id, "L12 FRAMES", "frames must be 4n+1")
        if method == "a14b" and frames > 81:
            add("ERROR", shot_id, "L12 LENGTH", "A14B shots stay at or under 81 frames (about 5 s). Split the beat.")
        if duration and duration < 1.5 and not shot.get("allow_short"):
            add("WARN", shot_id, "L12 SHORT", f"{duration:.2f} s reads as a glitch. Allow it explicitly or lengthen the cut.")
        for name in faces:
            base = str(name).split(" (")[0]
            registry = (doc.get("_lora_registry") or {}).get(base) or (doc.get("_lora_registry") or {}).get(name)
            if registry and registry.get("approved"):
                loaded = any(registry.get("file", "") in item.get("name", "") for item in shot.get("char_loras") or [])
                if not loaded:
                    add("WARN", shot_id, "L13 LORA", f"{name} has an approved LoRA ({registry.get('file')}) this shot does not load")
        moves = find(CAMERA_MOVE, f"{shot.get('camera', '')} {shot.get('action', '')}")
        if shot.get("anchor") == "pin" and moves:
            add("ERROR", shot_id, "L14 PIN_CAMERA", f"pin anchor plus a camera move ({moves}) reverses or freezes. Use anchor drift, or a locked-off camera.")
        if shot.get("anchor") == "pin" and not shot.get("motion"):
            add("WARN", shot_id, "L14 MOTION_PLAN", "pinned shot needs a motion field for what moves besides the camera: light, breath, eyes.")
    target = doc.get("target_runtime_s")
    if target and abs(total - float(target)) > 0.12 * float(target):
        add("WARN", "episode", "L15 RUNTIME", f"planned shots sum to {total:.1f} s vs target {target} s")
    return out, total


def _framing(cut: dict[str, Any]) -> str:
    if cut.get("source") == "card:time":
        return "CARD"
    return str(cut.get("framing") or "").upper()


def _ending_types(doc: dict[str, Any]) -> list[str]:
    ending = doc.get("ending")
    if isinstance(ending, str):
        return [ending]
    if isinstance(ending, list):
        return [str(item) for item in ending]
    if isinstance(ending, dict):
        raw = ending.get("type", [])
        if isinstance(raw, str):
            return [raw]
        return [str(item) for item in raw]
    return []


def lint_style_shots(doc: dict[str, Any]) -> list[Finding]:
    """L18, L19, L22, L23 on render shots (seed/v3/shotlint_v5.py)."""
    out: list[Finding] = []

    def add(level: Level, shot_id: str, rule: str, message: str) -> None:
        out.append(Finding(level, shot_id, rule, message))

    for shot in doc.get("shots") or []:
        shot_id = str(shot.get("id") or "?")
        prompt = str(shot.get("prompt") or "")
        if shot.get("kiss"):
            kiss = shot["kiss"]
            if kiss.get("angle") not in ("profile", "ots", "three_quarter"):
                add("ERROR", shot_id, "K01 KISS_STAGING", "kiss angle must be profile, ots, or three_quarter")
            if not kiss.get("soft_face"):
                add("ERROR", shot_id, "K01 KISS_STAGING", "one face must be partly turned or soft (soft_face)")
            if not (shot.get("start_frame") and shot.get("end_frame")):
                add("ERROR", shot_id, "K01 KISS_STAGING", "a kiss needs pinned start and end keyframes")
            if kiss.get("beat") == "kiss" and shot.get("kind") != "kiss":
                add("ERROR", shot_id, "K01 KISS_STAGING", "a kiss render uses gate kind=kiss")
            if kiss.get("beat") == "kiss" and len(shot.get("presets") or []) < 3:
                add("WARN", shot_id, "K01 KISS_STAGING", "give a kiss at least 3 presets")
        if not re.search(MOVE, f"{shot.get('camera', '')} {prompt}", re.I):
            add("ERROR", shot_id, "L18 CAMERA_MOVE", "render prompt has no camera move. Creep, drift, or dolly on every shot.")
        if re.search(LATERAL, str(shot.get("action") or ""), re.I):
            add("WARN", shot_id, "L19 LATERAL_WALK", "a lateral walk reads flat. Stage the step toward or away from the lens.")
        aerial = shot.get("v5_kind") == "aerial" or shot.get("kind") == "aerial"
        needed = ["rim", "glossy"] if aerial else ["rim", "glossy", "85mm"]
        missing = [word for word in needed if word not in prompt.lower()]
        if missing:
            add("ERROR", shot_id, "L22 STYLE_WORDS", f"prompt lacks {missing}")
        if shot.get("faces_visible") and "catchlight" not in prompt.lower():
            add("WARN", shot_id, "L22 STYLE_WORDS", "face shot without catchlights")
        beat = shot.get("beat")
        if shot.get("grade") == "desire" and not shot.get("kiss") and beat not in ("hook", "seduce", "kiss", "desire"):
            add("WARN", shot_id, "L23 GRADE", "warm desire grade outside a desire or hook beat")
    return out


def lint_cuts(doc: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []

    def add(level: Level, shot_id: str, rule: str, message: str) -> None:
        out.append(Finding(level, shot_id, rule, message))

    cuts = list(doc.get("cuts") or [])
    if not cuts:
        return [Finding("ERROR", "plan", "L16 MAX_CUT", "no cut list")]
    picture = [cut for cut in cuts if _framing(cut) != "CARD"]
    for index, cut in enumerate(cuts):
        cut_id = f"cut{int(cut.get('n') or index + 1):02d}"
        framing = _framing(cut)
        duration = float(cut.get("dur") or 0)
        beat = cut.get("beat")
        if framing == "CARD":
            continue
        if beat == "kiss":
            low, high = S.EDIT["kiss_cut_s"]
            if not (low <= duration <= high):
                add("ERROR", cut_id, "K02 KISS_LENGTH", f"kiss cut {duration} s (want {low}-{high} s)")
            nxt = cuts[index + 1] if index + 1 < len(cuts) else None
            if not nxt or _framing(nxt) not in ("CU", "ECU"):
                add("ERROR", cut_id, "K02 KISS_LENGTH", "a kiss is followed by a CU or ECU reaction")
        elif beat == "seduce":
            low, high = S.EDIT["seduce_cut_s"]
            if not (low <= duration <= high):
                add("WARN", cut_id, "K02 SEDUCE_LENGTH", f"seductive beat {duration} s (want {low}-{high} s)")
        elif duration > float(S.EDIT["max_cut_s"]) + 1e-6:
            add("ERROR", cut_id, "L16 MAX_CUT", f"{cut.get('source')} {duration} s is over {S.EDIT['max_cut_s']} s")
        if not cut.get("push"):
            add("ERROR", cut_id, "L18 CAMERA_MOVE", "picture cut has no push-in")
        if cut.get("grade") in ("desire", "flashback") and beat not in ("hook", "seduce", "kiss", "desire", "flashback"):
            add("WARN", cut_id, "L23 GRADE", "warm grade outside a desire, flashback, or hook beat")
        names = _forbidden_name_re(doc)
        for line in cut.get("lines") or []:
            speaker = str(line.get("speaker") or "").split(" (")[0]
            mode = line.get("mode") or cut.get("line_mode")
            visible = [str(name).split(" (")[0].lower() for name in cut.get("faces_visible") or []]
            if mode in ("os", "vo", "listener") and speaker and speaker.lower() in visible:
                add("ERROR", cut_id, "L24 SPEAKER", f"{speaker}'s off-screen line plays over {speaker}'s face")
            if names and names.search(str(line.get("text") or line.get("subtitle") or "")):
                add("ERROR", cut_id, "L25 NAMES", "a line uses a name on this project's forbidden_names list")
    if picture:
        close = sum(_framing(cut) in ("CU", "ECU") for cut in picture) / len(picture)
        level = "ERROR" if close < float(S.EDIT["min_cu_ratio"]) else "INFO"
        add(level, "plan", "L17 CU_RATIO", f"CU/ECU is {close:.0%} of {len(picture)} picture cuts (need >= {S.EDIT['min_cu_ratio']:.0%})")
    hook_low, hook_high = S.EDIT["hook_s"]
    first = cuts[0]
    if first.get("beat") != "hook" or not (hook_low <= float(first.get("dur") or 0) <= hook_high):
        add("ERROR", "plan", "L21 HOOK", f"cut 1 must be a hook of {hook_low}-{hook_high} s")
    if len(cuts) < 2 or cuts[1].get("source") != "card:time":
        add("WARN", "plan", "L21 TIME_CARD", "the hook should hard-cut to a time card")
    stakes = doc.get("stakes_at_s")
    if stakes is None or float(stakes) > float(S.EDIT["stakes_by_s"]):
        add("ERROR", "plan", "L21 STAKES", f"stakes line at {stakes} s (need one by {S.EDIT['stakes_by_s']:.0f} s)")
    types = _ending_types(doc)
    allowed = set(S.EDIT["endings"])
    if not types or any(item not in allowed for item in types):
        add("ERROR", "plan", "L20 ENDING", f"ending {types or None} must be one of {sorted(allowed)}")
    if cuts[-1].get("end_on") != "line_end":
        add("WARN", "plan", "L20 ENDING", "the last cut should end on the word of the final line (end_on: line_end)")
    names = _forbidden_name_re(doc)
    for name in doc.get("wardrobe_lock") or {}:
        if names and names.search(str(name)):
            add("ERROR", "plan", "L25 NAMES", f"forbidden name {name} is in the wardrobe lock")
    total = float(cuts[-1].get("at") or 0) + float(cuts[-1].get("dur") or 0)
    target = doc.get("target_runtime_s")
    if target and float(target) >= 80:
        if not (80 <= total <= 100):
            add("WARN", "plan", "L15 RUNTIME", f"{total:.1f} s (a full episode lands between 80 and 100)")
        if not (35 <= len(cuts) <= 45):
            add("WARN", "plan", "L15 CUTS", f"{len(cuts)} cuts (a 90 s episode lands between 35 and 45)")
    elif target and abs(total - float(target)) > 0.12 * float(target):
        add("WARN", "plan", "L15 RUNTIME", f"cuts sum to {total:.1f} s vs target {target} s")
    return out


def _forbidden_name_re(doc: dict[str, Any]) -> re.Pattern[str] | None:
    names = [str(item) for item in (doc.get("forbidden_names") or []) if str(item).strip()]
    if not names:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(name) for name in names) + r")\b", re.I)


def lint_plan(
    doc: dict[str, Any],
    root: Path | None = None,
    *,
    episode: int | None = None,
    allow_lipsync: bool | None = None,
) -> LintReport:
    if allow_lipsync is None:
        allow_lipsync = bool(doc.get("lipsync_approved"))
    root = Path(root or doc.get("root") or ".")
    findings, total = lint_shots(doc, episode=episode, allow_lipsync=allow_lipsync, root=root)
    by_id = {str(shot.get("id")): shot for shot in doc.get("shots") or []}
    kept: list[Finding] = []
    for item in findings:
        shot = by_id.get(item.shot_id, {})
        if shot.get("kiss") and item.rule in ("L03 CONTACT", "L03 TWO_FACES"):
            kept.append(Finding("INFO", item.shot_id, item.rule + " (kiss allowed)", "staged kiss: " + json.dumps(shot.get("kiss"))))
            continue
        if item.rule == "L12 SHORT" and shot.get("allow_short"):
            continue
        if item.rule == "L15 RUNTIME" and doc.get("cuts"):
            continue
        kept.append(item)
    kept.extend(lint_style_shots(doc))
    kept.extend(lint_cuts(doc))
    if doc.get("cuts"):
        total = float(doc["cuts"][-1].get("at") or 0) + float(doc["cuts"][-1].get("dur") or 0)
    return LintReport(kept, total)


def qc_spec(doc: dict[str, Any], shot: dict[str, Any]) -> dict[str, Any]:
    faces = list(shot.get("faces_visible") or [])
    characters = doc.get("characters") or {}
    refs = {}
    for name in faces:
        base = str(name).split(" (")[0]
        character = characters.get(name) or characters.get(base) or {}
        if character.get("sheet"):
            refs[base] = character["sheet"]
    return {
        "hands_allowed": bool(shot.get("allow_hands")),
        "faces_expected": len(faces),
        "photo_faces": int(shot.get("photo_faces") or 0),
        "mouth": "free" if shot.get("line_mode") == "lipsync_cu" or shot.get("mouth") == "free" else "closed",
        "min_face_gap": float(shot.get("min_face_gap") or 0.45),
        "need_s": shot.get("need_s"),
        "refs": refs,
        "sample_fps": 8,
        "identity_min": 0.30,
    }
