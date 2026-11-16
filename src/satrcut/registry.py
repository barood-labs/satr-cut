"""Character and location consistency.

Sheets, per-scene wardrobe, and optional identity locks (reference sheet,
IP-Adapter, PuLID, or a LoRA slot). The default ComfyUI render stays a plain
Wan graph. Identity nodes are a template you export, because those custom nodes
are not part of core ComfyUI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from satrcut.plan import generate_plan
from satrcut.project import Series


@dataclass(frozen=True)
class IdentityLock:
    character: str
    method: str
    sheet: str | None
    lora: str | None
    weight: float


def _character_block(series: Series, key: str) -> dict[str, Any]:
    raw = (series.raw.get("characters") or {}).get(key) or {}
    character = series.characters[key]
    identity = raw.get("identity") or {}
    lora = raw.get("lora") or {}
    return {
        "id": key,
        "display": character.name,
        "look": character.look,
        "sheet": character.sheet,
        "voice": character.voice,
        "identity": {
            "method": str(identity.get("method") or "sheet"),
            "weight": float(identity.get("weight") or 0.8),
        },
        "lora": {
            "file": lora.get("file"),
            "approved": bool(lora.get("approved")),
            "strength": float(lora.get("strength") or 0.8),
        },
    }


def registry_document(series: Series) -> dict[str, Any]:
    return {
        "series": series.name,
        "characters": {key: _character_block(series, key) for key in series.characters},
        "places": {
            key: {"id": key, "desc": place.desc, "sheet": place.sheet}
            for key, place in series.places.items()
        },
        "wardrobe": {place: dict(locked) for place, locked in series.wardrobe.items()},
    }


def lock_for(series: Series, character_id: str) -> IdentityLock:
    block = _character_block(series, character_id)
    return IdentityLock(
        character=character_id,
        method=block["identity"]["method"],
        sheet=block["sheet"],
        lora=block["lora"]["file"],
        weight=float(block["identity"]["weight"]),
    )


def apply_registry(series: Series, plan: dict[str, Any]) -> dict[str, Any]:
    """Stamp sheet paths and identity locks onto the plan. QC reads `qc.refs`."""
    plan = dict(plan)
    characters = dict(plan.get("characters") or {})
    for key, character in series.characters.items():
        block = characters.get(key) or {}
        block["sheet"] = character.sheet
        block["look"] = character.look
        block["display"] = character.name
        lock = lock_for(series, key)
        block["identity"] = {"method": lock.method, "weight": lock.weight, "lora": lock.lora}
        characters[key] = block
    plan["characters"] = characters
    plan["registry"] = registry_document(series)
    for shot in plan.get("shots") or []:
        refs = {}
        locks = []
        for name in shot.get("faces_visible") or []:
            base = str(name).split(" (")[0]
            character = series.characters.get(base)
            if character is None:
                continue
            if character.sheet:
                refs[base] = character.sheet
            lock = lock_for(series, base)
            locks.append({"character": base, "method": lock.method, "sheet": lock.sheet, "lora": lock.lora, "weight": lock.weight})
        qc = dict(shot.get("qc") or {})
        qc["refs"] = refs
        qc.setdefault("sample_fps", 8)
        shot["qc"] = qc
        shot["identity_locks"] = locks
    return plan


def apply_saved_plan(series: Series, episode: int) -> dict[str, Any]:
    from satrcut.project import episode_plan_path
    from satrcut.util import atomic_write_json, read_json

    path = episode_plan_path(series.root, episode)
    if path.is_file():
        plan = read_json(path)
    else:
        plan = generate_plan(series, episode)
    stamped = apply_registry(series, plan)
    atomic_write_json(path, stamped)
    return stamped
