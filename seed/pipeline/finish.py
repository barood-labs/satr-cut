"""Stage 0/3 helpers: audio padding, final mux, 480x848 timeline conform,
burned-subtitle 9:16 preview, and QC contact sheets. Only needs ffmpeg + Pillow."""
import json, subprocess
from pathlib import Path
from common import log, sh, sha256, ffprobe_json

FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]

def pad_audio(src, dst, offset_s, total_s, sr=48000):
    """Place the line at offset_s inside a silent bed exactly total_s long."""
    ms = int(round(offset_s * 1000))
    sh(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af",
        f"aresample={sr},adelay={ms}|{ms},apad,atrim=0:{total_s:.4f}", "-ac", "1", "-c:a", "pcm_s16le", str(dst)])
    return dst

def mux(video, audio, dst):
    sh(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(dst)])
    return dst

def conform_480x848(video, dst):
    sh(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", "0:v:0", "-vf",
        "scale=480:-2:flags=lanczos,crop=480:848", "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv444p", str(dst)])
    return dst

def burn_subtitle(video, text, dst, start_s=0.0, end_s=None):
    font = next((f for f in FONT_CANDIDATES if Path(f).exists()), None)
    if font is None:
        log("no font for subtitles; skipped")
        return None
    lines = text.split("\n")
    v = next(x for x in ffprobe_json(video)["streams"] if x.get("codec_type") == "video")
    W, H = v["width"], v["height"]
    fs = int(min(H / 34, 0.90 * W / (0.58 * max(len(l) for l in lines))))  # never wider than 90% of frame
    end = f"{end_s:.2f}" if end_s else "999"
    filt = []
    for i, ln in enumerate(lines):
        esc = ln.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace(",", "\\,")
        y = f"h*0.80+{i}*(th+14)-{len(lines)-1}*(th+14)/2"
        filt.append(f"drawtext=fontfile={font}:text='{esc}':fontsize={fs}:fontcolor=white:borderw=3:"
                    f"bordercolor=black@0.85:x=(w-tw)/2:y={y}:enable='between(t,{start_s:.2f},{end})'")
    sh(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", ",".join(filt), "-c:v", "libx264", "-crf", "16",
        "-pix_fmt", "yuv420p", "-c:a", "copy", str(dst)])
    return dst

def contact_sheet(video, dst, every_s=0.25, cols=6, width=200):
    import math
    dur = float(ffprobe_json(video).get("format", {}).get("duration", 5))
    rows = max(1, math.ceil(dur / every_s / cols))
    sh(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf",
        f"fps=1/{every_s},scale={width}:-2,tile={cols}x{rows}:padding=4:color=black", "-frames:v", "1", str(dst)])
    return dst

def mouth_strip(frames_dir, track_json, dst, step=2, cols=12, size=128):
    """Tile mouth crops from the tracked target face (every `step` frames)."""
    from PIL import Image
    tr = json.load(open(track_json))
    files = sorted(Path(frames_dir).glob("*.png"))
    tiles = []
    for i in range(0, min(len(files), len(tr["boxes"])), step):
        x1, y1, x2, y2 = tr["boxes"][i]
        im = Image.open(files[i]).convert("RGB")
        my = y1 + int((y2 - y1) * 0.55)
        tiles.append(im.crop((x1, my, x2, y2 + 10)).resize((size, size // 2)))
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * size, rows * size // 2), "black")
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % cols) * size, (k // cols) * size // 2))
    sheet.save(dst)
    return dst

def describe(path):
    p = ffprobe_json(path)
    v = next((s for s in p.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"path": str(path), "sha256": sha256(path), "duration": float(p.get("format", {}).get("duration", 0)),
            "size": [v.get("width"), v.get("height")], "frames": v.get("nb_frames"), "fps": v.get("r_frame_rate")}
