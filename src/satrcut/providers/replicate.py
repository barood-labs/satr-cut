"""Replicate video provider. Optional. The token is read from the environment and never stored."""

from __future__ import annotations

import os
from typing import Any

from satrcut.errors import SatrcutError
from satrcut.providers.base import RenderRequest, RenderResult


class ReplicateProvider:
    name = "replicate"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("SATRCUT_REPLICATE_MODEL") or "wan-video/wan-2.2-i2v-a14b"

    def configured(self) -> bool:
        return bool(os.environ.get("REPLICATE_API_TOKEN"))

    def healthy(self) -> bool:
        return self.configured()

    def restart(self) -> None:
        return None

    def payload(self, request: RenderRequest) -> dict[str, Any]:
        shot = request.shot
        # The token is a header, not a field. Image paths stay local until an upload step exists.
        return {
            "model": self.model,
            "input": {
                "prompt": shot.get("prompt") or "",
                "negative_prompt": shot.get("negative") or "",
                "seed": int(request.seed),
                "num_frames": int(shot.get("frames") or 49),
                "aspect_ratio": "9:16",
            },
        }

    def render(self, request: RenderRequest) -> RenderResult:
        if not self.configured():
            raise SatrcutError(
                "replicate needs REPLICATE_API_TOKEN in the environment. "
                "Nothing is billed and no token is written into the plan. Use --provider mock."
            )
        raise SatrcutError(
            "replicate is wired for a later upload step. The payload is built locally and no request is sent "
            "until an image-upload path is configured. Use ComfyUI or mock for a local file."
        )
