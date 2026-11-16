"""Video, speech, and transcription providers."""

from satrcut.providers.base import RenderRequest, RenderResult, VideoProvider
from satrcut.providers.comfyui_wan import ComfyUIWanProvider
from satrcut.providers.fal import FalProvider
from satrcut.providers.mock import MockProvider
from satrcut.providers.replicate import ReplicateProvider

__all__ = [
    "ComfyUIWanProvider",
    "FalProvider",
    "MockProvider",
    "RenderRequest",
    "RenderResult",
    "ReplicateProvider",
    "VideoProvider",
    "video_provider",
]


def video_provider(name: str) -> VideoProvider:
    key = (name or "mock").lower()
    if key == "mock":
        return MockProvider()
    if key in ("comfyui", "comfy", "wan", "ltx", "wan5b", "wan22-5b"):
        return ComfyUIWanProvider()
    if key == "fal":
        return FalProvider()
    if key == "replicate":
        return ReplicateProvider()
    raise ValueError(f"unknown video provider {name!r} (mock, comfyui, fal, replicate)")
