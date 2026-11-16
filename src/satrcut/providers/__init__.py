"""Video, speech, and transcription providers."""

from satrcut.providers.base import RenderRequest, RenderResult, VideoProvider
from satrcut.providers.comfyui_wan import ComfyUIWanProvider
from satrcut.providers.mock import MockProvider

__all__ = [
    "ComfyUIWanProvider",
    "MockProvider",
    "RenderRequest",
    "RenderResult",
    "VideoProvider",
    "video_provider",
]


def video_provider(name: str) -> VideoProvider:
    key = (name or "mock").lower()
    if key == "mock":
        return MockProvider()
    if key in ("comfyui", "comfy", "wan"):
        return ComfyUIWanProvider()
    raise ValueError(f"unknown video provider {name!r} (mock, comfyui)")
