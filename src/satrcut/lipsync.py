"""Close-up lip-sync is phase 2.

The in-house pass was 0 of 12 on MuseTalk, LatentSync, and InfiniteTalk close-ups.
Until a published fixture is green, lines stay on a listener, an off-screen read,
or the back of a head. This module is the interface those plugins will implement.
Nothing here loads a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from satrcut.errors import LipSyncUnavailable


@dataclass
class LipSyncResult:
    video: Path
    backend: str
    applied: bool


class LipSyncProvider:
    name = "lipsync"

    def available(self) -> bool:
        return False

    def apply(self, video: Path, audio: Path, dest: Path) -> LipSyncResult:
        raise LipSyncUnavailable(
            "Close-up lip-sync is phase 2 and off by default. "
            "Put the line on a listener, an O.S. read, or a back-of-head shot. "
            "satrcut lint rejects line_mode lipsync_cu unless the plan sets lipsync_approved."
        )


def get_lipsync() -> LipSyncProvider:
    return LipSyncProvider()
