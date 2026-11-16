"""Speech providers. Mock is the default and needs no key."""

from __future__ import annotations

import math
import random
import struct
import wave
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class TTSResult:
    path: Path
    duration_s: float
    voice: str
    text: str
    backend: str


class TTSProvider(Protocol):
    name: str

    def synthesize(self, text: str, voice: str, dest: Path, *, duration_s: float | None = None) -> TTSResult: ...


def _duration_for(text: str, cap: float | None) -> float:
    words = max(1, len(text.split()))
    duration = max(0.7, min(2.4, 0.34 * words))
    if cap is not None:
        duration = min(duration, max(0.6, cap))
    return duration


def write_wav(path: Path, samples: list[int], sample_rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, sample))) for sample in samples))


class MockTTS:
    """A sine tone plus a sidecar that holds the script line. The sidecar is the mock transcript."""

    name = "mock"

    def synthesize(self, text: str, voice: str, dest: Path, *, duration_s: float | None = None) -> TTSResult:
        duration = _duration_for(text, duration_s)
        sample_rate = 24000
        count = int(sample_rate * duration)
        freq = 160 + (zlib.adler32(voice.encode("utf-8")) % 280)
        samples: list[int] = []
        for index in range(count):
            envelope = min(1.0, index / (sample_rate * 0.02), (count - index) / (sample_rate * 0.04))
            value = 0.35 * envelope * math.sin(2 * math.pi * freq * index / sample_rate)
            samples.append(int(value * 32767))
        write_wav(dest, samples, sample_rate)
        dest.with_suffix(".txt").write_text(text, encoding="utf-8")
        return TTSResult(path=dest, duration_s=duration, voice=voice, text=text, backend=self.name)


def write_noise(path: Path, seconds: float = 0.28, sample_rate: int = 24000) -> Path:
    rng = random.Random(0)
    count = int(sample_rate * seconds)
    samples = []
    for index in range(count):
        decay = math.exp(-4 * index / count)
        samples.append(int(rng.uniform(-1, 1) * 12000 * decay))
    write_wav(path, samples, sample_rate)
    return path


def write_bed(path: Path, seconds: float = 40.0, sample_rate: int = 24000) -> Path:
    count = int(sample_rate * seconds)
    samples = []
    for index in range(count):
        value = 0.08 * math.sin(2 * math.pi * 110 * index / sample_rate)
        value += 0.05 * math.sin(2 * math.pi * 164.81 * index / sample_rate)
        samples.append(int(value * 32767))
    write_wav(path, samples, sample_rate)
    return path


def get_tts(name: str) -> TTSProvider:
    if name in ("mock", "", "none"):
        return MockTTS()
    raise ValueError(
        f"TTS provider {name!r} is not built in. Mock is the default. "
        "Kokoro and ElevenLabs are phase 2 and read keys only from the environment."
    )
