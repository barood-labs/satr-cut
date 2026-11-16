"""Provider contracts. Video, speech, and lip-sync share nothing but a name."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class RenderRequest:
    shot: dict[str, Any]
    seed: int
    preset: dict[str, Any]
    start_frame: Path
    end_frame: Path
    out_dir: Path


@dataclass
class RenderResult:
    mp4: Path
    seed: int
    render_s: float
    backend: str


class VideoProvider(Protocol):
    name: str

    def healthy(self) -> bool: ...

    def restart(self) -> None: ...

    def render(self, request: RenderRequest) -> RenderResult: ...
