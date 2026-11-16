"""Offline video provider.

Writes a 480x848 (or plan-sized) clip with a moving bar and the shot id.
No network, no GPU, no API key. The picture is obviously a stand-in.
"""

from __future__ import annotations

import time
from pathlib import Path

from satrcut.errors import InfraError
from satrcut.providers.base import RenderRequest, RenderResult
from satrcut.util import find_font, require_ok, x264_preset

GRADE_COLORS = {
    "desire": "0x5C301C",
    "danger": "0x101C30",
    "grief": "0x1C2028",
    "flashback": "0x604828",
    "neutral": "0x18181C",
}


class MockProvider:
    name = "mock"

    def healthy(self) -> bool:
        return True

    def restart(self) -> None:
        return None

    def render(self, request: RenderRequest) -> RenderResult:
        shot = request.shot
        width = int(shot.get("width") or 480)
        height = int(shot.get("height") or 848)
        fps = int(shot.get("fps") or 16)
        frames = int(shot.get("frames") or 33)
        duration = frames / fps
        request.out_dir.mkdir(parents=True, exist_ok=True)
        out = request.out_dir / f"take_s{request.seed}.mp4"
        font = find_font(bold=True)
        font_path = font.as_posix().replace(":", r"\:").replace("'", r"\'")
        label = str(shot.get("id") or "shot").replace(":", " ").replace("'", "")
        color = GRADE_COLORS.get(str(shot.get("grade") or "neutral"), GRADE_COLORS["neutral"])
        # A bar that travels the frame. This is real motion, not a still and not a zoom of frame 0.
        travel = max(1, width - 90)
        bar_h = max(80, height // 2)
        graph = (
            f"[0][1]overlay=x='20+{travel}*t/{duration:.4f}':y={height // 5},"
            f"drawtext=fontfile='{font_path}':text='{label}':fontsize=48:fontcolor=white:"
            f"borderw=3:bordercolor=black:x=(w-tw)/2:y=(h/2)[v]"
        )
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s={width}x{height}:r={fps}:d={duration:.4f}",
            "-f", "lavfi", "-i", f"color=c=0xF5D028:s=64x{bar_h}:r={fps}:d={duration:.4f}",
        ]
        audio = shot.get("line_audio")
        project_audio = Path(audio) if audio and Path(audio).is_file() else None
        if project_audio is not None:
            cmd += ["-i", str(project_audio)]
        cmd += ["-filter_complex", graph, "-map", "[v]"]
        if project_audio is not None:
            cmd += ["-map", "2:a:0", "-c:a", "aac", "-shortest"]
        cmd += ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", x264_preset(), "-crf", "23", str(out)]
        started = time.time()
        try:
            require_ok(cmd)
        except RuntimeError as exc:
            raise InfraError(str(exc)) from exc
        return RenderResult(mp4=out, seed=request.seed, render_s=round(time.time() - started, 3), backend=self.name)
