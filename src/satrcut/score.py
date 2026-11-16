"""Rate an episode against the dramabox preset.

Checks hook timing, average shot length, a cliffhanger plus a next-episode
stinger, a midpoint reversal, subtitle readability, and loudness when a file
exists. Misses are the point of the command. Nothing here calls a paid API.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from satrcut.export import delivery_for
from satrcut.project import episode_plan_path
from satrcut.util import read_json, run

HOOK_MIN = 2.5
HOOK_MAX = 3.0
SHOT_MIN = 1.5
SHOT_MAX = 2.5
LUFS_TARGET = -14.0
LUFS_TOLERANCE = 2.0


def _picture(cuts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [cut for cut in cuts if not str(cut.get("source") or "").startswith("card:") and str(cut.get("framing") or "").upper() != "CARD"]


def _measure_lufs(path: Path) -> float | None:
    result = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "loudnorm=print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    match = re.search(r"\{\s*\"input_i\".*\}", result.stderr or "", re.S)
    if not match:
        return None
    try:
        return float(json.loads(match.group(0))["input_i"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _subtitle_from_ass(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Style: Sub,"):
            parts = line.split(",")
            # Name, Fontname, Fontsize, PrimaryColour, ..., Outline, Shadow, Alignment, MarginL, MarginR, MarginV
            try:
                return {
                    "size": float(parts[2]),
                    "colour": parts[3],
                    "bold": parts[7],
                    "outline": float(parts[16]),
                    "margin_v": float(parts[21]),
                }
            except (IndexError, ValueError):
                return None
    return None


def score_episode(project: Path, episode: int = 1, *, video: Path | None = None) -> dict[str, Any]:
    plan_path = episode_plan_path(project, episode)
    plan = read_json(plan_path)
    spec = delivery_for(plan, "dramabox" if str(plan.get("style") or "") == "dramabox" else None)
    cuts = list(plan.get("cuts") or [])
    picture = _picture(cuts)
    misses: list[str] = []
    checks: dict[str, Any] = {}

    hook = cuts[0] if cuts else {}
    hook_dur = float(hook.get("dur") or 0)
    checks["hook_s"] = hook_dur
    if hook.get("beat") != "hook" or not (HOOK_MIN <= hook_dur <= HOOK_MAX):
        misses.append(f"hook: first cut is {hook_dur:.2f}s beat={hook.get('beat')!r} (want a hook of {HOOK_MIN}-{HOOK_MAX}s)")

    lengths = [float(cut.get("dur") or 0) for cut in picture if cut.get("beat") != "hook"]
    average = sum(lengths) / len(lengths) if lengths else 0
    checks["average_shot_s"] = round(average, 3)
    if not lengths or not (SHOT_MIN <= average <= SHOT_MAX):
        misses.append(f"pacing: average shot is {average:.2f}s (want {SHOT_MIN}-{SHOT_MAX}s)")
    long = [cut for cut in picture if cut.get("beat") != "hook" and float(cut.get("dur") or 0) > SHOT_MAX + 1e-6]
    if long:
        misses.append(f"pacing: {len(long)} shot(s) run past {SHOT_MAX}s")

    ending = plan.get("ending")
    if isinstance(ending, dict):
        ending = ending.get("type")
    story = [cut for cut in cuts if not str(cut.get("source") or "").startswith("card:")]
    cliff = bool(story) and story[-1].get("end_on") == "line_end" and ending in {
        "uncompleted_action",
        "reveal",
        "demand",
        "entrance",
        "confession",
        "interruption",
    }
    stinger = any(cut.get("source") == "card:next" for cut in cuts)
    checks["cliffhanger"] = cliff
    checks["next_episode"] = stinger
    if not cliff:
        misses.append("cliffhanger: the episode does not end on an uncompleted action or reveal")
    if not stinger:
        misses.append("stinger: missing the next-episode card after the cliffhanger")

    reversal = any(cut.get("beat") == "reversal" for cut in cuts)
    checks["midpoint_reversal"] = reversal
    if picture and not reversal:
        misses.append("reversal: no emotional reversal around the midpoint")

    ass = project / "deliverables" / f"ep{episode:02d}.ass"
    style = _subtitle_from_ass(ass)
    safe = spec["safe"]
    if style is None:
        style = {
            "size": safe.get("size", 68),
            "colour": "&H0000FFFF",
            "bold": "-1",
            "outline": safe.get("outline", 6),
            "margin_v": safe.get("margin_v", 500),
        }
        checks["subtitles"] = "preset"
    else:
        checks["subtitles"] = "ass"
    colour = str(style["colour"]).upper()
    if style["size"] < 48 or style["outline"] < 3 or "FFFF" not in colour:
        misses.append(
            f"subtitles: size {style['size']}, outline {style['outline']}, colour {style['colour']} "
            "(want bold yellow with a black outline, size >= 48, outline >= 3)"
        )
    margin = float(style["margin_v"])
    checks["subtitle_margin_v"] = margin
    if not (360 <= margin <= 640):
        misses.append(f"subtitles: margin {margin:.0f}px is outside the lower safe area")

    mp4 = video or (project / "deliverables" / f"ep{episode:02d}.mp4")
    if mp4.is_file():
        measured = _measure_lufs(mp4)
        checks["lufs"] = measured
        if measured is None:
            misses.append("loudness: could not read integrated loudness")
        elif abs(measured - LUFS_TARGET) > LUFS_TOLERANCE:
            misses.append(f"loudness: {measured:.1f} LUFS (want {LUFS_TARGET:.0f} ± {LUFS_TOLERANCE:.0f})")
    else:
        checks["lufs"] = None
        checks["loudness"] = "not measured (no mp4)"

    return {
        "episode": episode,
        "preset": "dramabox",
        "ok": not misses,
        "misses": misses,
        "checks": checks,
        "runtime_s": plan.get("runtime_s"),
        "trope": plan.get("trope"),
        "paywall": bool(plan.get("paywall")),
    }
