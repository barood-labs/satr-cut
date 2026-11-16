"""Objective lip-sync plausibility: mouth opening (inner-lip gap / mouth width, from 68
landmarks on the OUTPUT frames, same target face) vs the dialogue loudness envelope.
Reports Pearson r at the best lag within +-4 frames (positive lag = mouth after audio)."""
import json, sys
from pathlib import Path
import numpy as np, cv2, librosa, face_alignment

def main(frames_dir, audio, fps, target_xy, out_json):
    fps = float(fps); tx, ty = map(float, target_xy.split(","))
    lt = getattr(face_alignment.LandmarksType, "TWO_D", None) or face_alignment.LandmarksType._2D
    fa = face_alignment.FaceAlignment(lt, device="cpu", face_detector="sfd")
    files = sorted(Path(frames_dir).glob("*.png")); prev = None; op = []
    for f in files:
        im = cv2.imread(str(f)); h, w = im.shape[:2]
        res = fa.get_landmarks_from_image(cv2.cvtColor(im, cv2.COLOR_BGR2RGB)) or []
        ref = prev if prev is not None else np.array([tx * w, ty * h])
        if not res: op.append(np.nan); continue
        lm = min(res, key=lambda l: np.linalg.norm(l.mean(0) - ref)); prev = lm.mean(0)
        gap = np.linalg.norm(lm[66] - lm[62]); width = np.linalg.norm(lm[64] - lm[60]) + 1e-6
        op.append(gap / width)
    op = np.array(op); op = np.where(np.isnan(op), np.nanmean(op), op)
    y, sr = librosa.load(audio, sr=16000)
    hop = int(sr / fps); rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop, center=True)[0][:len(op)]
    n = min(len(rms), len(op)); rms, op = rms[:n], op[:n]
    best = None
    for lag in range(-4, 5):
        a = rms[max(0, -lag): n - max(0, lag)]; b = op[max(0, lag): n - max(0, -lag) if lag < 0 else n]
        m = min(len(a), len(b)); r = float(np.corrcoef(a[:m], b[:m])[0, 1])
        if best is None or r > best[1]: best = (lag, r)
    speech = rms > 0.25 * rms.max()
    rep = {"frames": int(n), "best_lag_frames": best[0], "pearson_r": round(best[1], 3),
           "mouth_open_mean_speech": round(float(op[speech].mean()), 3),
           "mouth_open_mean_silence": round(float(op[~speech].mean()), 3) if (~speech).any() else None,
           "mouth_open_max": round(float(op.max()), 3), "opening": [round(float(x), 3) for x in op]}
    json.dump(rep, open(out_json, "w"), indent=1); print({k: v for k, v in rep.items() if k != "opening"})

if __name__ == "__main__":
    main(*sys.argv[1:6])
