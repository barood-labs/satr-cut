"""Locked start and end stills.

The mock provider draws a labeled still when a frame is missing. A real
production replaces those files and registers the sha256. Checks that need
MediaPipe run when it is installed and otherwise report which checks were skipped.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from satrcut.errors import SatrcutError
from satrcut.util import atomic_write_json, read_json, sha256_file

GRADE_RGB = {
    "desire": ((92, 48, 28), (140, 78, 40)),
    "danger": ((16, 28, 48), (12, 42, 72)),
    "grief": ((28, 32, 40), (20, 28, 44)),
    "flashback": ((96, 72, 40), (120, 96, 56)),
    "neutral": ((24, 24, 28), (36, 36, 40)),
}
FRAMING = {
    "single_cu": "Vertical 9:16 close-up from the collarbones up. Only the face, neck, and collar are in frame.",
    "ecu": "Vertical 9:16 extreme close-up. The eyes fill the upper half. Lips stay in frame. No hands.",
    "cu": "Vertical 9:16 close-up from the collarbones up. Only the face, neck, and collar are in frame.",
    "ots": "Vertical 9:16 over-the-shoulder. The near shoulder is soft. One face is sharp, with a clear gap.",
    "two_shot_wide": "Vertical 9:16 medium two-shot. The two people stay an arm's length apart. Faces do not touch.",
    "insert": "Vertical 9:16 macro of the object only. No people.",
    "establishing": "Vertical 9:16 empty establishing frame. Doors closed. No readable text.",
}


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    from satrcut.util import find_font

    try:
        return ImageFont.truetype(str(find_font(bold=True)), size)
    except (OSError, RuntimeError):
        return ImageFont.load_default()


def manifest_path(project: Path) -> Path:
    return project / "keyframes" / "manifest.json"


def load_manifest(project: Path) -> dict[str, Any]:
    path = manifest_path(project)
    if not path.exists():
        return {"keyframes": []}
    return read_json(path)


def draw_still(
    path: Path,
    *,
    shot_id: str,
    role: str,
    grade: str = "neutral",
    size: tuple[int, int] = (480, 848),
    caption: str = "",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pair = GRADE_RGB.get(grade, GRADE_RGB["neutral"])
    color = pair[0] if role == "start" else pair[1]
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    bar_x = 36 if role == "start" else size[0] - 110
    draw.rectangle((bar_x, 80, bar_x + 64, size[1] - 80), fill=(245, 208, 40))
    font = _font(42)
    small = _font(22)
    draw.text((32, size[1] // 2 - 70), shot_id, fill=(255, 255, 255), font=font)
    draw.text((32, size[1] // 2 - 10), role.upper(), fill=(245, 208, 40), font=font)
    if caption:
        draw.text((32, size[1] // 2 + 50), caption[:42], fill=(220, 220, 220), font=small)
    image.save(path)
    return path


def draw_sheet(path: Path, *, name: str, look: str, size: tuple[int, int] = (480, 848)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (32, 36, 48))
    draw = ImageDraw.Draw(image)
    draw.ellipse((140, 150, 340, 390), fill=(196, 164, 140))
    draw.rectangle((120, 400, 360, 760), fill=(48, 56, 72))
    draw.text((40, 80), name, fill=(255, 255, 255), font=_font(48))
    draw.text((40, 800), "sheet", fill=(245, 208, 40), font=_font(22))
    y = 620
    for word in look.split():
        draw.text((40, y), word, fill=(210, 210, 210), font=_font(18))
        y += 22
        if y > 760:
            break
    image.save(path)
    return path


def ensure_sheets(project: Path, plan: dict[str, Any]) -> None:
    for key, character in (plan.get("characters") or {}).items():
        sheet = character.get("sheet")
        if not sheet:
            continue
        path = project / sheet
        if path.exists():
            continue
        draw_sheet(path, name=str(character.get("display") or key), look=str(character.get("look") or ""))


def ensure_pair(project: Path, shot: dict[str, Any]) -> tuple[Path, Path]:
    size = (int(shot.get("width") or 480), int(shot.get("height") or 848))
    start = project / str(shot["start_frame"])
    end = project / str(shot["end_frame"])
    caption = str(shot.get("shot_type") or "")
    if not start.exists():
        draw_still(start, shot_id=shot["id"], role="start", grade=str(shot.get("grade") or "neutral"), size=size, caption=caption)
    if not end.exists():
        if shot.get("anchor") == "pin":
            start_image = Image.open(start).convert("RGB")
            end.parent.mkdir(parents=True, exist_ok=True)
            start_image.save(end)
        else:
            drift_end(start, end, scale=1.06)
    return start, end


def fit(src: Path, dst: Path, size: tuple[int, int] = (480, 848)) -> Path:
    from PIL import ImageOps

    dst.parent.mkdir(parents=True, exist_ok=True)
    ImageOps.fit(Image.open(src).convert("RGB"), size, Image.Resampling.LANCZOS).save(dst)
    return dst


def crop_tighter(src: Path, dst: Path, bottom: float = 0.12, size: tuple[int, int] = (480, 848)) -> Path:
    """Drop the bottom of the frame after a HAND fail, then refit 9:16. At most twice."""
    image = Image.open(src).convert("RGB")
    width, height = image.size
    new_height = int(height * (1 - bottom))
    new_width = int(new_height * size[0] / size[1])
    x0 = max(0, (width - new_width) // 2)
    cropped = image.crop((x0, 0, x0 + min(new_width, width), new_height))
    cropped.resize(size, Image.Resampling.LANCZOS).save(dst)
    return dst


def drift_end(src: Path, dst: Path, scale: float = 1.04, dx: float = 0.0, dy: float = -0.01, size: tuple[int, int] | None = None) -> Path:
    """End frame for a drift anchor: the start frame, reframed slightly tighter."""
    image = Image.open(src).convert("RGB")
    if size is None:
        size = image.size
    image = image.resize(size, Image.Resampling.LANCZOS)
    width, height = size
    crop_w, crop_h = int(width / scale), int(height / scale)
    cx, cy = width / 2 + dx * width, height / 2 + dy * height
    x0 = int(min(max(0, cx - crop_w / 2), width - crop_w))
    y0 = int(min(max(0, cy - crop_h / 2), height - crop_h))
    image.crop((x0, y0, x0 + crop_w, y0 + crop_h)).resize(size, Image.Resampling.LANCZOS).save(dst)
    return dst


def check_image(path: Path, shot: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "flags": [], "skipped": []}
    if not path.exists():
        result["flags"].append("MISSING")
        result["ok"] = False
        return result
    with Image.open(path) as image:
        width, height = image.size
    result["size"] = [width, height]
    if height == 0 or abs((width / height) - (9 / 16)) > 0.04:
        result["flags"].append(f"ASPECT {width}x{height} is not 9:16")
    try:
        import mediapipe as mp  # type: ignore
    except ImportError:
        result["skipped"].append("hands")
        mp = None
    if mp is not None:
        import numpy as np

        rgb = np.array(Image.open(path).convert("RGB"))
        with mp.solutions.hands.Hands(static_image_mode=True, max_num_hands=2, min_detection_confidence=0.5) as hands:
            detected = hands.process(rgb)
        count = len(detected.multi_hand_landmarks or [])
        result["hands"] = count
        allowed = bool((shot or {}).get("allow_hands"))
        if count and not allowed:
            result["flags"].append(f"HANDS_IN_KEYFRAME ({count})")
    result["ok"] = not result["flags"]
    return result


def register(project: Path, image: Path, shot_id: str, role: str, *, shot: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    checks = check_image(image, shot)
    if not checks["ok"] and not force:
        raise SatrcutError("keyframe check failed: " + ", ".join(checks["flags"]))
    rel = str(image.relative_to(project)) if image.is_relative_to(project) else str(image)
    data = load_manifest(project)
    data["keyframes"] = [row for row in data.get("keyframes", []) if not (row.get("shot") == shot_id and row.get("role") == role)]
    data["keyframes"].append(
        {
            "path": rel.replace("\\", "/"),
            "sha256": sha256_file(image),
            "shot": shot_id,
            "role": role,
            "bytes": image.stat().st_size,
        }
    )
    atomic_write_json(manifest_path(project), data)
    return checks


def write_briefs(project: Path, plan: dict[str, Any]) -> list[Path]:
    folder = project / "keyframes" / "briefs"
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for shot in plan.get("shots") or []:
        shot_type = str(shot.get("shot_type") or "cu")
        frame = FRAMING.get(shot_type, FRAMING["cu"])
        place = (plan.get("places") or {}).get(shot.get("place") or "", {})
        who = []
        for name in shot.get("in_frame") or []:
            character = (plan.get("characters") or {}).get(name) or {}
            who.append(f"{character.get('display') or name}: {character.get('look') or 'the person in the reference sheet'}.")
        brief = {
            "shot": shot["id"],
            "shot_type": shot_type,
            "aspect": "9:16",
            "anchor": shot.get("anchor"),
            "reference_images": [((plan.get("characters") or {}).get(name) or {}).get("sheet") for name in shot.get("in_frame") or []],
            "prompt": " ".join(
                part
                for part in (
                    frame,
                    plan.get("film_look") or "",
                    f"Place: {place.get('desc', '')}." if place else "",
                    " ".join(who),
                    f"Moment: {shot.get('action') or ''}",
                    "Lips sealed unless this is an approved talking close-up.",
                )
                if part
            ),
            "rules": [
                "no hands in frame",
                "faces of different people stay at least one face width apart",
                "doors stay closed",
                "no readable text",
                "wardrobe matches the scene lock",
            ],
        }
        path = folder / f"{shot['id']}.json"
        atomic_write_json(path, brief)
        (folder / f"{shot['id']}.md").write_text(f"# {shot['id']} ({shot_type})\n\n{brief['prompt']}\n", encoding="utf-8")
        written.append(path)
    return written


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
