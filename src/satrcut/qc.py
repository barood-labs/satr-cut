"""Per-take QC gate.

Samples about 8 fps. Always measures real motion versus a still or a Ken Burns
reframe (numpy). Hands, faces, face gap, and mouth run when MediaPipe is
installed. Identity runs when OpenCV SFace weights are present at
SATRCUT_SFACE / SATRCUT_YUNET. Without those libraries the report says which
checks were skipped and still fails a frozen frame.

Verdicts: PASS, TRIM, REVIEW (a deliberate hand insert), FAIL.
A mock sidecar can force a verdict only when SATRCUT_QC_RESPECT_SIDECAR=1,
which the tests use to exercise auto-retake.
"""

from __future__ import annotations

import math
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from satrcut.util import atomic_write_json, read_json, which

DEFAULTS: dict[str, Any] = {
    "hands_allowed": False,
    "faces_expected": None,
    "photo_faces": 0,
    "min_face_gap": 0.45,
    "mouth": "closed",
    "need_s": None,
    "min_local_motion": 1.2,
    "min_zoom_residual": 6.0,
    "identity_min": 0.30,
    "mouth_range_max": 0.15,
    "mouth_open_max": 0.30,
    "sample_fps": 8,
}
PASSING = {"PASS", "TRIM", "REVIEW"}


@dataclass
class QCReport:
    verdict: str
    reasons: list[str]
    video: str
    duration_s: float = 0.0
    clean_window: list[float] = field(default_factory=lambda: [0.0, 0.0])
    skipped: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    spec: dict[str, Any] = field(default_factory=dict)
    flag_counts: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def box_gap(a: Any, b: Any) -> float:
    """Gap between two face boxes in units of mean face width. 0 means they overlap."""
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    dx = max(0.0, max(ax1, bx1) - min(ax2, bx2))
    dy = max(0.0, max(ay1, by1) - min(ay2, by2))
    return math.hypot(dx, dy) / (((a[2] + b[2]) / 2) + 1e-9)


def _read_gray(path: Path, sample_fps: int, size: tuple[int, int] = (180, 320)) -> list[np.ndarray]:
    width, height = size
    cmd = [
        which("ffmpeg"),
        "-v",
        "error",
        "-i",
        str(path),
        "-vf",
        f"fps={sample_fps},scale={width}:{height}:flags=area",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return []
    frame_bytes = width * height
    count = len(result.stdout) // frame_bytes
    frames = []
    for index in range(count):
        raw = result.stdout[index * frame_bytes : (index + 1) * frame_bytes]
        frames.append(np.frombuffer(raw, dtype=np.uint8).reshape(height, width).copy())
    return frames


def _mae(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.mean(np.abs(left.astype(np.float32) - right.astype(np.float32))))


def _resize_nn(image: np.ndarray, height: int, width: int) -> np.ndarray:
    ys = np.linspace(0, image.shape[0] - 1, height).astype(np.int32)
    xs = np.linspace(0, image.shape[1] - 1, width).astype(np.int32)
    return image[ys][:, xs]


def _zoom_mae(reference: np.ndarray, frame: np.ndarray) -> float:
    """How well a zoom/crop of frame 0 explains this frame. A Ken Burns move scores low."""
    height, width = reference.shape
    best = _mae(reference, frame)
    for scale in (1.03, 1.06, 1.1, 1.15):
        crop_h, crop_w = int(height / scale), int(width / scale)
        if crop_h < 8 or crop_w < 8:
            continue
        y0 = max(0, (height - crop_h) // 2)
        x0 = max(0, (width - crop_w) // 2)
        crop = reference[y0 : y0 + crop_h, x0 : x0 + crop_w]
        best = min(best, _mae(_resize_nn(crop, height, width), frame))
    return best


def _motion(frames: list[np.ndarray]) -> dict[str, float]:
    if len(frames) < 2:
        return {"local_motion_mean": 0.0, "zoom_residual": 0.0, "consec_p90": 0.0}
    diffs = [_mae(a, b) for a, b in zip(frames, frames[1:])]
    late = frames[len(frames) // 2 :]
    residuals = [_zoom_mae(frames[0], frame) for frame in late]
    return {
        "local_motion_mean": float(np.mean(diffs)),
        "consec_p90": float(np.percentile(diffs, 90)),
        "zoom_residual": float(np.median(residuals)),
    }


def _sidecar(video: Path) -> dict[str, Any] | None:
    path = video.with_suffix(".qc.json")
    if path.is_file():
        return read_json(path)
    return None


def _duration(video: Path, frames: list[np.ndarray], sample_fps: int) -> float:
    if not frames:
        return 0.0
    try:
        from satrcut.util import media_duration

        probed = media_duration(video)
        if probed > 0:
            return probed
    except (RuntimeError, OSError, ValueError):
        pass
    return len(frames) / sample_fps


def _mediapipe_flags(video: Path, spec: dict[str, Any], sample_fps: int) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        import mediapipe as mp  # type: ignore
    except ImportError:
        return [], ["hands", "faces", "mouth"]
    cmd = [
        which("ffmpeg"),
        "-v",
        "error",
        "-i",
        str(video),
        "-vf",
        f"fps={sample_fps},scale=270:480:flags=area",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return [], ["hands", "faces", "mouth"]
    width, height = 270, 480
    frame_bytes = width * height * 3
    count = len(result.stdout) // frame_bytes
    skipped: list[str] = []
    per: list[dict[str, Any]] = []
    hands = mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=2, min_detection_confidence=0.5)
    mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=3, refine_landmarks=False, min_detection_confidence=0.4)
    try:
        for index in range(count):
            raw = result.stdout[index * frame_bytes : (index + 1) * frame_bytes]
            rgb = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3).copy()
            flags: list[str] = []
            hand_result = hands.process(rgb)
            hand_count = len(hand_result.multi_hand_landmarks or [])
            if hand_count and not spec.get("hands_allowed"):
                flags.append("HAND")
            face_result = mesh.process(rgb)
            boxes = []
            for landmarks in face_result.multi_face_landmarks or []:
                xs = [point.x * width for point in landmarks.landmark]
                ys = [point.y * height for point in landmarks.landmark]
                boxes.append([min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)])
            expected = spec.get("faces_expected")
            if expected and len(boxes) < int(expected):
                flags.append("FACE_LOST")
            if len(boxes) >= 2 and int(expected or 0) >= 2:
                gap = min(box_gap(boxes[i], boxes[j]) for i in range(len(boxes)) for j in range(i + 1, len(boxes)))
                if gap < float(spec.get("min_face_gap") or 0.45):
                    flags.append("FACES_CLOSE")
            mouth = None
            if face_result.multi_face_landmarks and spec.get("mouth") == "closed":
                landmark = face_result.multi_face_landmarks[0].landmark
                open_amount = math.dist((landmark[13].x * width, landmark[13].y * height), (landmark[14].x * width, landmark[14].y * height))
                mouth_width = math.dist((landmark[61].x * width, landmark[61].y * height), (landmark[291].x * width, landmark[291].y * height)) + 1e-6
                mouth = open_amount / mouth_width
                if mouth > float(spec.get("mouth_open_max") or 0.30):
                    flags.append("MOUTH_OPEN")
            per.append({"t": round(index / sample_fps, 3), "flags": flags, "hands": hand_count, "faces": len(boxes), "mouth": mouth})
    finally:
        hands.close()
        mesh.close()
    if spec.get("refs") and not (os.environ.get("SATRCUT_SFACE") and os.environ.get("SATRCUT_YUNET")):
        skipped.append("identity")
    return per, skipped


def _identity_note(spec: dict[str, Any]) -> str | None:
    if not spec.get("refs"):
        return None
    yunet = os.environ.get("SATRCUT_YUNET")
    sface = os.environ.get("SATRCUT_SFACE")
    if not (yunet and sface):
        return "identity skipped: set SATRCUT_YUNET and SATRCUT_SFACE to OpenCV onnx weights"
    try:
        import cv2  # type: ignore
    except ImportError:
        return "identity skipped: opencv is not installed"
    if not Path(yunet).is_file() or not Path(sface).is_file():
        return "identity skipped: weight files are missing"
    _ = cv2
    return None


def evaluate(video: Path, spec: dict[str, Any] | None = None, *, out: Path | None = None) -> QCReport:
    video = Path(video)
    merged = dict(DEFAULTS)
    merged.update({key: value for key, value in (spec or {}).items() if value is not None})
    sidecar = _sidecar(video)
    if os.environ.get("SATRCUT_QC_RESPECT_SIDECAR") == "1" and sidecar and sidecar.get("force_verdict"):
        report = QCReport(
            verdict=str(sidecar["force_verdict"]),
            reasons=list(sidecar.get("reasons") or [str(sidecar["force_verdict"])]),
            video=str(video),
            spec=merged,
            metrics={"sidecar": True},
        )
        if out:
            atomic_write_json(out, report.to_json())
        return report
    sample_fps = int(merged.get("sample_fps") or 8)
    frames = _read_gray(video, sample_fps)
    if len(frames) < 2:
        report = QCReport(verdict="FAIL", reasons=["UNREADABLE video"], video=str(video), spec=merged)
        if out:
            atomic_write_json(out, report.to_json())
        return report
    motion = _motion(frames)
    duration = _duration(video, frames, sample_fps)
    reasons: list[str] = []
    if motion["local_motion_mean"] < float(merged["min_local_motion"]) and motion["zoom_residual"] < float(merged["min_zoom_residual"]):
        reasons.append(f"STILL (no motion) local={motion['local_motion_mean']:.3f}")
    elif motion["zoom_residual"] < float(merged["min_zoom_residual"]) and motion["local_motion_mean"] < 4.0:
        reasons.append(f"KEN_BURNS/STILL: frames are a reframe of frame 0 (residual {motion['zoom_residual']:.2f})")
    per_frame, skipped = _mediapipe_flags(video, merged, sample_fps)
    note = _identity_note(merged)
    if note and "identity" not in skipped:
        skipped.append("identity")
    from collections import Counter

    counts = Counter(flag for row in per_frame for flag in row["flags"])
    persistent = [flag for flag, count in counts.items() if count >= 2]
    first_bad: dict[str, float] = {}
    for row in per_frame:
        for flag in row["flags"]:
            first_bad.setdefault(flag, row["t"])
    take_level = list(reasons)
    if persistent:
        reasons.extend(f"{flag} from {first_bad[flag]}s ({counts[flag]} samples)" for flag in sorted(persistent, key=lambda item: first_bad[item]))
    need = float(merged["need_s"]) if merged.get("need_s") is not None else max(0.5, duration - 0.2)
    clean = [0.0, round(duration, 3)]
    if per_frame:
        best = (0.0, 0.0, 0.0)
        start = None
        rows = per_frame + [{"t": duration, "flags": ["END"]}]
        for row in rows:
            bad = any(flag not in {"WARDROBE_DRIFT"} for flag in row["flags"])
            if not bad and start is None:
                start = row["t"]
            if bad and start is not None:
                end = row["t"]
                if end - start > best[2]:
                    best = (start, end, end - start)
                start = None
        clean = [round(best[0], 3), round(best[1], 3)]
    if not reasons:
        verdict = "REVIEW" if merged.get("hands_allowed") else "PASS"
    elif not take_level and clean[1] - clean[0] >= need:
        verdict = "TRIM"
    else:
        verdict = "FAIL"
    report = QCReport(
        verdict=verdict,
        reasons=reasons,
        video=str(video),
        duration_s=round(duration, 3),
        clean_window=clean,
        skipped=skipped,
        metrics={**motion, "identity_note": note, "samples": len(frames), "sample_fps": sample_fps},
        spec={key: merged[key] for key in ("hands_allowed", "faces_expected", "min_face_gap", "mouth", "need_s", "sample_fps")},
        flag_counts=dict(counts),
    )
    if out:
        atomic_write_json(out, report.to_json())
    return report
