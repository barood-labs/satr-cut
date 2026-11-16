"""Synthesize lines and check each one against the script."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from satrcut.providers.asr import ASRResult, get_asr
from satrcut.providers.tts import get_tts, write_bed, write_noise
from satrcut.util import atomic_write_json


def voice_for(plan: dict[str, Any], speaker: str) -> str:
    base = (speaker or "").split(" (")[0].strip().lower()
    for key, character in (plan.get("characters") or {}).items():
        display = str(character.get("display") or key).lower()
        if key.lower() == base or display == base:
            return str(character.get("voice") or f"mock:{key}")
    return "mock:narrator"


def synthesize_plan(project: Path, plan: dict[str, Any]) -> dict[str, Any]:
    providers = plan.get("providers") or {}
    tts = get_tts(str(providers.get("tts") or "mock"))
    asr_name = str(providers.get("asr") or "mock")
    try:
        asr = get_asr(asr_name)
    except RuntimeError as exc:
        asr = get_asr("mock")
        asr_note = str(exc)
    else:
        asr_note = ""
    lines: list[dict[str, Any]] = []
    mismatches = 0
    for shot in plan.get("shots") or []:
        text = shot.get("line")
        if not text or not shot.get("line_audio"):
            continue
        dest = project / str(shot["line_audio"])
        cap = max(0.6, float(shot.get("duration_s") or 2) - 0.35)
        spoken = tts.synthesize(str(text), voice_for(plan, str(shot.get("speaker") or "")), dest, duration_s=cap)
        heard: ASRResult = asr.transcribe(dest, str(text))
        if not heard.ok:
            mismatches += 1
        lines.append(
            {
                "shot": shot["id"],
                "file": shot["line_audio"],
                "voice": spoken.voice,
                "duration_s": round(spoken.duration_s, 3),
                "expected": text,
                "transcript": heard.text,
                "ok": heard.ok,
                "asr": heard.backend,
            }
        )
        for effect in shot.get("sfx") or []:
            if effect.get("synth") == "noise" and effect.get("file"):
                write_noise(project / str(effect["file"]))
    music = plan.get("music")
    if music:
        write_bed(project / str(music))
    report = {"backend_tts": tts.name, "backend_asr": asr.name, "note": asr_note, "mismatches": mismatches, "lines": lines}
    atomic_write_json(project / "audio" / "asr_report.json", report)
    return report
