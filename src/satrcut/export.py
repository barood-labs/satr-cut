"""Delivery presets and subtitle shaping.

Export presets are 1080x1920 with a safe area and -14 LUFS, which is the
loudness short-video platforms expect. Arabic lines stay in logical order and
are wrapped in Unicode RTL embedding marks so libass and HarfBuzz shape them
with an Arabic font. The text is not reversed by hand.
"""

from __future__ import annotations

import re
from typing import Any

ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")

PRESETS: dict[str, dict[str, Any]] = {
    "master": {
        "label": "Master",
        "width": None,
        "height": None,
        "fps": None,
        "lufs": -16.0,
        "safe": {"margin_v": 420, "margin_l": 80, "margin_r": 80},
        "font": "DejaVu Sans",
    },
    "tiktok": {
        "label": "TikTok",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "lufs": -14.0,
        "safe": {"margin_v": 560, "margin_l": 90, "margin_r": 160},
        "font": "DejaVu Sans",
    },
    "youtube_shorts": {
        "label": "YouTube Shorts",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "lufs": -14.0,
        "safe": {"margin_v": 480, "margin_l": 80, "margin_r": 80},
        "font": "DejaVu Sans",
    },
    "reels": {
        "label": "Instagram Reels",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "lufs": -14.0,
        "safe": {"margin_v": 540, "margin_l": 100, "margin_r": 160},
        "font": "DejaVu Sans",
    },
    "reelshort": {
        "label": "ReelShort-style",
        "width": 1080,
        "height": 1920,
        "fps": 24,
        "lufs": -14.0,
        "safe": {"margin_v": 460, "margin_l": 80, "margin_r": 80},
        "font": "DejaVu Sans",
    },
}


def preset(name: str | None) -> dict[str, Any]:
    key = (name or "master").lower().replace("-", "_")
    if key in ("shorts", "youtube"):
        key = "youtube_shorts"
    if key not in PRESETS:
        known = ", ".join(PRESETS)
        raise ValueError(f"unknown export preset {name!r} ({known})")
    return dict(PRESETS[key])


def is_arabic(text: str, lang: str = "en") -> bool:
    return lang.lower().startswith("ar") or bool(ARABIC.search(text or ""))


def shape_subtitle(text: str, lang: str = "en") -> str:
    """Logical-order text plus RTL embedding when the line is Arabic."""
    if not text:
        return text
    if is_arabic(text, lang):
        if text.startswith("\u202b"):
            return text
        return "\u202b" + text + "\u202c"
    return text


def subtitle_font(lang: str) -> str:
    if lang.lower().startswith("ar"):
        return "Noto Naskh Arabic"
    return "DejaVu Sans"


def loudnorm(lufs: float) -> str:
    return f"loudnorm=I={lufs:.0f}:TP=-1.5:LRA=11"


def delivery_for(plan: dict[str, Any], name: str | None) -> dict[str, Any]:
    chosen = preset(name)
    base = plan.get("delivery") or {}
    return {
        "preset": (name or "master").lower(),
        "width": int(chosen["width"] or base.get("width") or 1080),
        "height": int(chosen["height"] or base.get("height") or 1920),
        "fps": int(chosen["fps"] or base.get("fps") or 24),
        "lufs": float(chosen["lufs"]),
        "safe": dict(chosen["safe"]),
        "lang": str(plan.get("lang") or "en"),
    }
