"""fal.ai video provider. Optional. The key is read from FAL_KEY and never stored."""

from __future__ import annotations

import os
from typing import Any

from satrcut.errors import SatrcutError
from satrcut.providers.base import RenderRequest, RenderResult


class FalProvider:
    name = "fal"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("SATRCUT_FAL_MODEL") or "fal-ai/wan/v2.2-a14b/image-to-video"

    def configured(self) -> bool:
        return bool(os.environ.get("FAL_KEY"))

    def healthy(self) -> bool:
        return self.configured()

    def restart(self) -> None:
        return None

    def payload(self, request: RenderRequest) -> dict[str, Any]:
        shot = request.shot
        return {
            "prompt": shot.get("prompt") or "",
            "negative_prompt": shot.get("negative") or "",
            "seed": int(request.seed),
            "aspect_ratio": "9:16",
            "num_frames": int(shot.get("frames") or 49),
            "image_path": str(request.start_frame),
            "end_image_path": str(request.end_frame) if request.end_frame else None,
        }

    def render(self, request: RenderRequest) -> RenderResult:
        if not self.configured():
            raise SatrcutError("fal needs FAL_KEY in the environment. Nothing is billed and no key is written into the plan. Use --provider mock.")
        # Building the payload must not submit it. A POST would spend credit.
        _ = self.payload(request)
        raise SatrcutError(
            "fal payload is built locally. v0.2 does not POST it, so a key in the environment cannot spend credit. "
            "Use --provider mock or comfyui for a file on disk."
        )
