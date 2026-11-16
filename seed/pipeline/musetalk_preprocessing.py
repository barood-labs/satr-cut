"""Drop-in replacement for MuseTalk's musetalk/utils/preprocessing.py.

Why: the upstream file needs mmpose/mmcv (DWPose) only to get 68 face landmarks,
and mmcv has no wheels for current Kaggle images. face-alignment (BSD-3, S3FD +
FAN) gives the same iBUG-68 landmark order, so the bbox math below is identical
to upstream (half-face point = landmark 29, lower bound = max landmark y).

Additions for two-person drama shots:
- picks ONE target face: the face nearest SATRCUT_FACE_TARGET (normalised x,y) on
  the first frame, then the face nearest the previous centre on each later frame;
- never returns the placeholder bbox (upstream silently desyncs frames when a
  face is missed); misses hold the last good box;
- temporal smoothing of the box (window SATRCUT_BBOX_SMOOTH) to cut jitter.
Per-frame landmarks are written to SATRCUT_LANDMARKS_JSON for QC.
"""
import json, os
import cv2
import numpy as np
import torch
from tqdm import tqdm
import face_alignment

coord_placeholder = (0.0, 0.0, 0.0, 0.0)
_dev = "cuda" if torch.cuda.is_available() else "cpu"
_lt = getattr(face_alignment.LandmarksType, "TWO_D", None) or getattr(face_alignment.LandmarksType, "_2D")
fa = face_alignment.FaceAlignment(_lt, flip_input=False, device=_dev, face_detector="sfd")


def read_imgs(img_list):
    print("reading images...")
    return [cv2.imread(p) for p in tqdm(img_list)]


def _box_from_landmarks(lm, upperbondrange=0):
    lm = lm.astype(np.int32)
    half = lm[29].copy()
    if upperbondrange != 0:
        half[1] = upperbondrange + half[1]
    half_dist = np.max(lm[:, 1]) - half[1]
    upper = max(0, half[1] - half_dist)
    return [int(np.min(lm[:, 0])), int(upper), int(np.max(lm[:, 0])), int(np.max(lm[:, 1]))]


def get_landmark_and_bbox(img_list, upperbondrange=0):
    frames = read_imgs(img_list)
    tx, ty = [float(v) for v in os.environ.get("SATRCUT_FACE_TARGET", "0.5,0.4").split(",")]
    win = int(os.environ.get("SATRCUT_BBOX_SMOOTH", "5"))
    boxes, lms, prev_c, misses = [], [], None, 0
    for fr in tqdm(frames):
        h, w = fr.shape[:2]
        res = fa.get_landmarks_from_image(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
        pick = None
        if res:
            ref = prev_c if prev_c is not None else np.array([tx * w, ty * h])
            cands = [(np.linalg.norm(l.mean(0) - ref), l) for l in res]
            d, l = min(cands, key=lambda c: c[0])
            # reject a jump to the other actor's face (more than 1/4 frame width)
            if prev_c is None or d < 0.25 * w:
                pick = l
        if pick is None:
            misses += 1
            boxes.append(boxes[-1] if boxes else None)
            lms.append(None)
            continue
        prev_c = pick.mean(0)
        boxes.append(_box_from_landmarks(pick, upperbondrange))
        lms.append(pick.round(1).tolist())
    first = next((b for b in boxes if b is not None), None)
    if first is None:
        raise RuntimeError("no target face found in any frame")
    boxes = [b if b is not None else first for b in boxes]
    arr = np.array(boxes, dtype=np.float64)
    if win > 1:
        k = np.ones(win) / win
        pad = np.pad(arr, ((win // 2, win - 1 - win // 2), (0, 0)), mode="edge")
        arr = np.stack([np.convolve(pad[:, i], k, mode="valid") for i in range(4)], 1)
    coords = [tuple(int(round(v)) for v in b) for b in arr]
    coords = [(max(0, x1), max(0, y1), x2, y2) for x1, y1, x2, y2 in coords]
    out = os.environ.get("SATRCUT_LANDMARKS_JSON")
    if out:
        json.dump({"target": [tx, ty], "misses": misses, "boxes": coords, "landmarks68": lms}, open(out, "w"))
    print(f"target-face boxes: {len(coords)} frames, {misses} misses held")
    return coords, frames


def get_bbox_range(img_list, upperbondrange=0):
    return "range not computed (satrcut preprocessing)"
