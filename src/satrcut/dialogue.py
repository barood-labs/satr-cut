"""Put spoken lines where a mouth can fail without showing it.

If a lip-sync plugin is missing or its mouth-sync score is under the gate, the
line moves to a listener, an off-screen read, or a back-of-head shot. The
plugins do not download weights and do not run unless both the install path and
the score clear the gate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from satrcut import style
from satrcut.errors import LipSyncUnavailable
from satrcut.lint import lint_plan
from satrcut.project import episode_plan_path
from satrcut.util import atomic_write_json, read_json


@dataclass
class MouthSync:
    plugin: str
    available: bool
    score: float | None
    decision: str  # unavailable | route | apply


class _Plugin:
    def __init__(self, name: str, env: str, min_score: float = 0.75) -> None:
        self.name = name
        self.env = env
        self.min_score = min_score

    def available(self) -> bool:
        root = os.environ.get(self.env)
        return bool(root and Path(root).expanduser().exists())

    def decide(self, score: float | None) -> MouthSync:
        if not self.available():
            return MouthSync(self.name, False, score, "unavailable")
        if score is None or score < self.min_score:
            return MouthSync(self.name, True, score, "route")
        return MouthSync(self.name, True, score, "apply")

    def apply(self, video: Path, audio: Path, dest: Path, score: float | None = None) -> MouthSync:
        decision = self.decide(score)
        if decision.decision != "apply":
            raise LipSyncUnavailable(
                f"{self.name} is gated (decision={decision.decision}, score={score}, minimum={self.min_score}). "
                "The line stays on a listener, off-screen, or the back of a head."
            )
        raise LipSyncUnavailable(f"{self.name} passed the score gate but v0.2 does not run the model binary yet.")


def plugins() -> list[_Plugin]:
    return [_Plugin("latentsync", "SATRCUT_LATENTSYNC"), _Plugin("musetalk", "SATRCUT_MUSETALK")]


def route_shot(shot: dict[str, Any], reason: str) -> dict[str, Any]:
    """Move a line off the speaker's mouth. Returns a new shot dict."""
    updated = dict(shot)
    speaker = str(updated.get("speaker") or "").split(" (")[0].strip()
    faces = [str(name) for name in updated.get("faces_visible") or []]
    kept = [name for name in faces if name.split(" (")[0].lower() != speaker.lower()]
    updated["faces_visible"] = kept
    updated["line_mode"] = "listener" if kept else "vo"
    if speaker and "(O.S.)" not in str(updated.get("speaker") or "") and "(V.O.)" not in str(updated.get("speaker") or ""):
        updated["speaker"] = f"{speaker} (O.S.)"
    action = str(updated.get("action") or "")
    if "lips sealed" not in action.lower():
        action = (action + " Lips sealed, jaw set.").strip()
    updated["action"] = action
    updated["lipsync_routed"] = reason
    kind = updated.get("kind") if updated.get("kind") in ("insert", "aerial") else "character"
    updated["prompt"] = style.prompt(str(kind), str(updated.get("camera") or ""), action, str(updated.get("grade") or "neutral"))
    return updated


def route_plan(plan: dict[str, Any], *, force: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Route any lipsync_cu line, and any shot whose QC mouth check failed."""
    notes = []
    shots = []
    for shot in plan.get("shots") or []:
        qc = shot.get("qc_result") or {}
        reasons = " ".join(qc.get("reasons") or [])
        mouth_failed = "MOUTH" in reasons or "LIPSYNC" in reasons
        if force or shot.get("line_mode") == "lipsync_cu" or mouth_failed:
            if shot.get("line") or shot.get("subtitle"):
                routed = route_shot(shot, "mouth-sync gate" if mouth_failed else "lipsync_cu is off")
                notes.append(f"{shot['id']}: {shot.get('line_mode')} -> {routed['line_mode']}")
                shots.append(routed)
                continue
        shots.append(shot)
    plan = dict(plan)
    plan["shots"] = shots
    # Keep cut line modes aligned with the shots the cuts came from.
    by_id = {shot["id"]: shot for shot in shots}
    for cut in plan.get("cuts") or []:
        source = str(cut.get("source") or "")
        shot_id = cut.get("shot_id") or (source.split(":", 1)[1] if ":" in source else "")
        shot = by_id.get(shot_id)
        if not shot:
            continue
        cut["line_mode"] = shot.get("line_mode")
        cut["faces_visible"] = list(shot.get("faces_visible") or [])
        for line in cut.get("lines") or []:
            line["mode"] = shot.get("line_mode")
            if shot.get("speaker"):
                line["speaker"] = shot["speaker"]
    return plan, notes


def route_saved(project: Path, episode: int) -> tuple[dict[str, Any], list[str]]:
    path = episode_plan_path(project, episode)
    plan, notes = route_plan(read_json(path))
    report = lint_plan(plan, project)
    if report.errors:
        plan, _passes = __import__("satrcut.write", fromlist=["autofix"]).autofix(plan, project)
        report = lint_plan(plan, project)
        if report.errors:
            raise LipSyncUnavailable("routing left lint errors:\n" + report.render())
    atomic_write_json(path, plan)
    return plan, notes
