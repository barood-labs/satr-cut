"""ASR check. Mock reads the script line the TTS step wrote. It does not 'fix' the line."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class ASRResult:
    text: str
    expected: str
    ok: bool
    backend: str
    note: str = ""


class ASRProvider(Protocol):
    name: str

    def transcribe(self, wav: Path, expected: str) -> ASRResult: ...


def normalize(text: str) -> str:
    text = text.replace("'", "").replace("’", "").replace("'", "")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text.lower()).strip()


def lines_match(expected: str, heard: str) -> bool:
    return normalize(expected) == normalize(heard)


class MockASR:
    """Returns the script line stored next to the wav. A mismatch is a flag, never a rewrite."""

    name = "mock"

    def transcribe(self, wav: Path, expected: str) -> ASRResult:
        sidecar = wav.with_suffix(".txt")
        heard = sidecar.read_text(encoding="utf-8").strip() if sidecar.is_file() else expected
        return ASRResult(text=heard, expected=expected, ok=lines_match(expected, heard), backend=self.name)


class WhisperASR:
    """Optional faster-whisper check. Skipped unless that package is installed."""

    name = "whisper"

    def __init__(self) -> None:
        try:
            import faster_whisper  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("faster-whisper is not installed; use asr: mock or pip install satrcut[asr]") from exc

    def transcribe(self, wav: Path, expected: str) -> ASRResult:
        from faster_whisper import WhisperModel  # type: ignore

        model_name = __import__("os").environ.get("SATRCUT_WHISPER_MODEL", "tiny")
        model = WhisperModel(model_name, device="cpu")
        segments, _info = model.transcribe(str(wav))
        heard = " ".join(segment.text for segment in segments).strip()
        return ASRResult(
            text=heard,
            expected=expected,
            ok=lines_match(expected, heard),
            backend=self.name,
            note="flag only; the script line is not rewritten",
        )


def get_asr(name: str) -> ASRProvider:
    if name in ("mock", "", "none"):
        return MockASR()
    if name in ("whisper", "faster-whisper"):
        return WhisperASR()
    raise ValueError(f"unknown ASR provider {name!r}")
