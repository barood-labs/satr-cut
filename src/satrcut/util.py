"""Small file, hash, and ffmpeg helpers. No machine-specific paths."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
)


def sha256_file(path: Path, bufsize: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(bufsize), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def which(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} is not on PATH")
    return found


def run(cmd: list[str], *, cwd: Path | None = None, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True, timeout=timeout)


def require_ok(cmd: list[str], *, cwd: Path | None = None, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    result = run(cmd, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-2000:]
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}\n{tail}")
    return result


def ffprobe(path: Path) -> dict[str, Any]:
    result = require_ok(
        [which("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    )
    return json.loads(result.stdout or "{}")


def media_duration(path: Path) -> float:
    info = ffprobe(path)
    return float((info.get("format") or {}).get("duration") or 0.0)


def video_size(path: Path) -> tuple[int, int]:
    for stream in ffprobe(path).get("streams") or []:
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    return 0, 0


def find_font(*, bold: bool = False) -> Path:
    ordered = list(FONT_CANDIDATES)
    if not bold:
        ordered = [p for p in ordered if "Bold" not in p and "arialbd" not in p] + ordered
    for candidate in ordered:
        if Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError("no TTF font found (install DejaVu Sans or Arial)")


def ffmpeg_filter_path(path: Path) -> str:
    text = Path(path).resolve().as_posix().replace("'", r"\'")
    return text.replace(":", r"\:")


def free_gb(path: Path) -> float:
    target = Path(path)
    while not target.exists() and target.parent != target:
        target = target.parent
    return shutil.disk_usage(target).free / 1e9


def x264_preset() -> str:
    return os.environ.get("SATRCUT_X264_PRESET", "veryfast")


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
