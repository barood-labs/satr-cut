"""'shadow' backend for satrcut.py: render on the user's GPU host (Windows, RTX A4500) instead of Kaggle.

  satrcut.py shadow-prep --ids 4-06 [--model 5b|a14b] [--override overrides.json] [--seed N]
      -> shadow_jobs/<job>/ (job.json, start_<id>.png, audio_<id>_padded.wav) + shadow_jobs/<job>.zip
  (agent) CopyFromBox <job>.zip -> os.environ.get("SATRCUT_ROOT", str(Path.home() / "satrcut"))\\jobs\\<job>.zip, expand, then on the PC:
      os.environ.get("SATRCUT_ROOT", str(Path.home() / "satrcut"))\\ComfyUI_windows_portable\\python_embeded\\python.exe
          os.environ.get("SATRCUT_ROOT", str(Path.home() / "satrcut"))\\own-tool\\shadow\\runner_shadow.py os.environ.get("SATRCUT_ROOT", str(Path.home() / "satrcut"))\\jobs\\<job>
  (agent) CopyToBox ...\\jobs\\<job>\\satrcut-output.zip -> box
  satrcut.py shadow-ingest <zip>   -> takes/<shot>/tNN (+ the take's merged shot), then postfix + qc as usual.
Overrides (JSON {"<id>": {field: value}}) let a test take change start frame, framing, model, etc. without
editing the episode shot list; the merged shot is stored on the take and used by postfix/qc."""
import json, shutil, time
from pathlib import Path
from common import build_prompt, lint_shot, log
import finish

A14B = {"model": "a14b", "width": 720, "height": 1280, "fps": 16, "frames": 81}
INFINITETALK = {"model": "infinitetalk", "width": 576, "height": 1024, "fps": 25, "frames": 125}
PRESETS = {"a14b": A14B, "infinitetalk": INFINITETALK}
SILENT_NEG = ", talking, speaking, open mouth, moving lips, teeth, chewing"

def merged_shot(doc, shot, override):
    m = json.loads(json.dumps(shot)); m.update(override or {})
    if m.get("model") in PRESETS:
        for k, v in PRESETS[m["model"]].items():
            if k in (override or {}): continue
            if k == "frames" and m["model"] == "infinitetalk" and shot.get("fps") == 25 and shot.get("frames", 999) <= 245: continue  # IT chains windows: keep shot length
            m[k] = v
    if "prompt" not in (override or {}):
        if m.get("silent_mouth"):   # Wan pass for a MuseTalk shot: keep the mouth closed/still, lip-sync adds speech
            m["prompt"] = build_prompt(doc, dict(m, line=None)); m["negative"] = m["negative"] + SILENT_NEG
        else:
            m["prompt"] = build_prompt(doc, m)
    errs = lint_shot(doc, m)
    if errs: raise ValueError(f"{m['id']} override breaks rules: {errs}")
    return m

def prep(here, doc, shots, model="5b", overrides=None, seed=None, name=None):
    overrides = overrides or {}
    name = name or f"job_{time.strftime('%Y%m%d_%H%M%S')}_{'_'.join(s['id'] for s in shots)[:40]}"
    jd = here / "shadow_jobs" / name; jd.mkdir(parents=True, exist_ok=True)
    job = {"shots": [], "stages": ["wan", "lipsync"], "created": time.strftime("%F %T")}
    for s in shots:
        ov = dict(overrides.get(s["id"], {}))
        if model != "5b": ov.setdefault("model", model)
        m = merged_shot(doc, s, ov)
        if seed is not None: m["seeds"] = [seed]
        if str(m["start_frame"]).startswith("@"): raise ValueError("@prev_last is not supported by shadow-prep yet; give a start frame")
        src = here / m["start_frame"]
        from PIL import Image, ImageOps   # exact job resolution, Lanczos, centre crop (same 9:16 in practice)
        ImageOps.fit(Image.open(src).convert("RGB"), (m["width"], m["height"]), Image.LANCZOS).save(jd / f"start_{m['id']}.png")
        if m.get("end_frame"):   # optional first+last-frame target (a14b only)
            ImageOps.fit(Image.open(here / m["end_frame"]).convert("RGB"), (m["width"], m["height"]), Image.LANCZOS).save(jd / f"end_{m['id']}.png")
        if m.get("audio"):
            finish.pad_audio(here / m["audio"], jd / f"audio_{m['id']}_padded.wav", m.get("audio_offset_s", 0.2), m["frames"] / m["fps"])
        else:
            m["no_audio"] = True   # silent / O.S. shot: Wan only, no lip-sync
        job["shots"].append(m)
    (jd / "job.json").write_text(json.dumps(job, indent=1, ensure_ascii=False), encoding="utf-8")
    zp = shutil.make_archive(str(jd), "zip", jd)
    log("shadow job", jd, "->", zp)
    return jd, Path(zp)
