"""Load a series bible and an episode beat sheet."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class RenderSize:
    width: int = 480
    height: int = 848
    fps: int = 16


@dataclass
class DeliverySize:
    width: int = 1080
    height: int = 1920
    fps: int = 24


@dataclass
class Character:
    id: str
    look: str
    unique_detail: str = ""
    voice: str = "mock:default"
    sheet: str | None = None
    display: str = ""

    @property
    def name(self) -> str:
        return self.display or self.id[:1].upper() + self.id[1:]


@dataclass
class Place:
    id: str
    desc: str
    sheet: str | None = None


@dataclass
class Series:
    name: str
    root: Path
    aspect: str = "9:16"
    render: RenderSize = field(default_factory=RenderSize)
    delivery: DeliverySize = field(default_factory=DeliverySize)
    target_duration_s: float = 90
    style: str = "dramabox"
    hook_s: float = 3
    endings: list[str] = field(default_factory=list)
    characters: dict[str, Character] = field(default_factory=dict)
    places: dict[str, Place] = field(default_factory=dict)
    wardrobe: dict[str, dict[str, str]] = field(default_factory=dict)
    providers: dict[str, str] = field(default_factory=dict)
    forbidden_names: list[str] = field(default_factory=list)
    music: str | None = "audio/bed.wav"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.root / "series.yaml"


def find_project(start: Path | None = None) -> Path:
    env = os.environ.get("SATRCUT_PROJECT")
    if env:
        return Path(env).expanduser().resolve()
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "series.yaml").is_file():
            return candidate
    return current


def _size(data: dict[str, Any] | None, fallback: RenderSize | DeliverySize) -> Any:
    data = data or {}
    kind = type(fallback)
    return kind(
        width=int(data.get("width", fallback.width)),
        height=int(data.get("height", fallback.height)),
        fps=int(data.get("fps", fallback.fps)),
    )


def load_series(project: Path | None = None) -> Series:
    root = find_project(project)
    path = root / "series.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"no series.yaml under {root}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    characters: dict[str, Character] = {}
    for key, value in (raw.get("characters") or {}).items():
        value = value or {}
        characters[str(key)] = Character(
            id=str(key),
            look=str(value.get("look") or ""),
            unique_detail=str(value.get("unique_detail") or ""),
            voice=str(value.get("voice") or f"mock:{key}"),
            sheet=value.get("sheet"),
            display=str(value.get("display") or ""),
        )
    places = {
        str(key): Place(id=str(key), desc=str((value or {}).get("desc") or ""), sheet=(value or {}).get("sheet"))
        for key, value in (raw.get("places") or {}).items()
    }
    wardrobe = {
        str(place): {str(who): str(look) for who, look in (locked or {}).items()}
        for place, locked in (raw.get("wardrobe") or {}).items()
    }
    providers = {str(k): str(v) for k, v in (raw.get("providers") or {}).items()}
    providers.setdefault("video", "mock")
    providers.setdefault("tts", "mock")
    providers.setdefault("asr", "mock")
    return Series(
        name=str(raw.get("name") or root.name),
        root=root,
        aspect=str(raw.get("aspect") or "9:16"),
        render=_size(raw.get("render"), RenderSize()),
        delivery=_size(raw.get("delivery"), DeliverySize()),
        target_duration_s=float(raw.get("target_duration_s") or 90),
        style=str(raw.get("style") or "dramabox"),
        hook_s=float(raw.get("hook_s") or 3),
        endings=[str(item) for item in (raw.get("endings") or [])],
        characters=characters,
        places=places,
        wardrobe=wardrobe,
        providers=providers,
        forbidden_names=[str(item) for item in (raw.get("forbidden_names") or [])],
        music=raw.get("music", "audio/bed.wav"),
        raw=raw,
    )


def episode_yaml_path(project: Path, episode: int) -> Path:
    return project / "episodes" / f"ep{episode:02d}.yaml"


def episode_plan_path(project: Path, episode: int) -> Path:
    return project / "episodes" / f"ep{episode:02d}.plan.json"


def load_episode_yaml(project: Path, episode: int) -> dict[str, Any]:
    path = episode_yaml_path(project, episode)
    if not path.is_file():
        raise FileNotFoundError(f"missing episode file {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a mapping")
    data["episode"] = int(data.get("episode") or episode)
    return data


def data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"
