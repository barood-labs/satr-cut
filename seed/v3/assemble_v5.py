#!/usr/bin/env python3
"""Assemble a DramaBox-style episode from a plan JSON (see build_example_plan.py).
Sources per cut: v4:<id> -> pulled v4 manifest (chosen take; FAIL_ALL allowed only with --allow-fail), v5:<id> -> pulled v5
manifest, keep:<id> -> passed earlier take, card:time -> generated. Missing source -> PLACEHOLDER (start->end keyframe blend,
flagged in the report; never ship a placeholder). Every cut: trim -> optional slow-mo -> crop-in -> slow digital push-in ->
colour code grade (warm only for desire/flashback, cool for danger/grief) -> phone drawtext overlays.
Then: concat, line/SFX/heartbeat/breath mix on the absolute timeline, burned-in ASS: centred yellow subtitles with black
outline, serif (Cinzel) name cards on first appearance, red cursive (Alex Brush) time card.
usage: assemble_v5.py [--v4 DIR] [--v5 DIR] [--out MP4] [--allow-fail] [--placeholders]"""
import argparse, json, os, subprocess, sys, shutil, math
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE)); import style as S
ROOT = Path(os.environ.get("SATRCUT_PROJECT", str(Path.cwd())))
OT = Path(os.environ.get("SATRCUT_ROOT", str(HERE.parent)))
KF = OT / "v3" / "keyframes" / "shots"
FPS = 24; W, H = 720, 1280
def run(cmd): r = subprocess.run(cmd, capture_output=True, text=True); assert r.returncode == 0, (cmd, r.stderr[-1500:]); return r
def dur_of(p): return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]).stdout.strip())

def resolve(src, dirs, allow_fail, placeholders, tmp, report):
    kind, sid = src.split(":")
    if kind == "keep": return None
    d = dirs.get(kind)
    if d:
        man = Path(d) / sid / "manifest.json"
        if man.exists():
            m = json.loads(man.read_text()); ch = m.get("chosen") or {}
            if ch.get("mp4") and (m.get("status") == "PASS" or allow_fail):
                p = Path(d) / ch["mp4"].replace("\\", "/").split("out/", 1)[-1]
                if p.exists(): report.append((src, m.get("status"), str(p))); return p
            report.append((src, m.get("status", "?") + " (not usable)", ""))
    if not placeholders: raise SystemExit(f"no usable take for {src}; rerun with --placeholders for a test cut")
    ph = tmp / f"ph_{sid}.mp4"
    if not ph.exists():
        a, b = KF / f"{sid}_start.jpg", KF / f"{sid}_end.jpg"
        if not b.exists(): b = a          # 5B shots (ep2+) have a start keyframe only
        run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", "5.1", "-i", str(a), "-loop", "1", "-t", "5.1", "-i", str(b), "-filter_complex",
             f"[0]scale={W}:{H},setsar=1,fps=16[a];[1]scale={W}:{H},setsar=1,fps=16[b];[a][b]xfade=transition=fade:duration=4.0:offset=0.5,format=yuv420p",
             "-t", "5.06", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(ph)])
    report.append((src, "PLACEHOLDER", str(ph))); return ph

def esc(t): return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")

def segment(c, src, out, fontdir):
    a, b = c["piece"]; sp = c.get("slowmo", 1.0); D = c["dur"]; z0, z1 = c.get("push", [1.0, 1.045]); cr = c.get("crop", 1.0)
    vf = []
    if sp != 1.0: vf.append(f"setpts=PTS/{sp}")
    vf += [f"fps={FPS}", f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}"]
    zs = f"({cr}*({z0}+({z1}-{z0})*min(t/{D},1)))"
    vf += [f"scale=w='trunc({W}*{zs}/2)*2':h='trunc({H}*{zs}/2)*2':eval=frame", f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2"]
    vf.append(S.GRADE_FX.get(c["grade"], S.GRADE_FX["neutral"]))
    ov = c.get("overlay")
    if ov:
        f1 = fontdir["sans"]; vf.append(f"drawtext=fontfile='{f1}':text='{esc(ov['title'])}':fontsize=54:fontcolor=white:x=(w-tw)/2:y=h*0.36:shadowx=2:shadowy=2:alpha='min(1,t/0.4)'")
        vf.append(f"drawtext=fontfile='{f1}':text='{esc(ov['sub'])}':fontsize=30:fontcolor=white@0.85:x=(w-tw)/2:y=h*0.36+70:alpha='min(1,t/0.4)'")
    vf += ["setsar=1", "format=yuv420p"]
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-i", str(src), "-t", f"{(b - a):.3f}", "-an", "-vf", ",".join(vf),
         "-t", f"{D:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", str(out)])
    got = dur_of(out)
    if got < D - 0.06:   # source shorter than the piece: hold the last frame
        tmp = out.with_suffix(".pad.mkv"); run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-vf", f"tpad=stop_mode=clone:stop_duration={D - got + 0.05:.3f}", "-t", f"{D:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", str(tmp)]); tmp.replace(out)

def card(c, out):
    D = c["dur"]
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={D}", "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "17", str(out)])

def ts(t): h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60; return f"{h}:{m:02d}:{s:05.2f}"

def ass(plan, path):
    E = S.EDIT; sub, nc, tc = E["subtitle"], E["name_card"], E["time_card"]
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{sub['font']},{sub['size']},{sub['colour']},&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{sub['outline']},1,{sub['align']},60,60,{sub['margin_v']},1
Style: Name,{nc['font']},{nc['size']},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,6,0,1,2,2,8,40,40,170,1
Style: NameSub,{nc['sub_font']},{nc['sub_size']},&H00E0E0E0,&H000000FF,&H00000000,&H64000000,0,-1,0,0,100,100,2,0,1,1.5,1,8,40,40,250,1
Style: Time,{tc['font']},{tc['size']},{tc['colour']},&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,0,3,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    for c in plan["cuts"]:
        t0 = c["at"]
        if c["source"] == "card:time":
            ev.append(f"Dialogue: 1,{ts(t0)},{ts(t0 + c['dur'])},Time,,0,0,0,,{{\\fad(150,200)\\t(0,{int(c['dur']*1000)},\\fscx108\\fscy108)}}{c['text']}")
        if c.get("name"):
            n, role = c["name"]
            ev.append(f"Dialogue: 2,{ts(t0 + 0.15)},{ts(t0 + min(c['dur'], 2.2))},Name,,0,0,0,,{{\\fad(200,250)}}{n}")
            if role and role != "?": ev.append(f"Dialogue: 2,{ts(t0 + 0.3)},{ts(t0 + min(c['dur'], 2.2))},NameSub,,0,0,0,,{{\\fad(200,250)}}{role}")
        for l in c.get("lines", []):
            txt = l.get("subtitle", "")
            if not txt or "[voicemail]" in txt: continue
            a = t0 + l["at"]; d = dur_of(ROOT / l["file"]) if (ROOT / l["file"]).exists() else 2.0
            ev.append(f"Dialogue: 0,{ts(a)},{ts(a + d + 0.25)},Sub,,0,0,0,,{txt}")
        for a, b, txt in c.get("subs", []):
            ev.append(f"Dialogue: 0,{ts(t0 + a)},{ts(t0 + b)},Sub,,0,0,0,,{txt.split(': ', 1)[-1]}")
    Path(path).write_text(hdr + "\n".join(ev) + "\n")

def heartbeat(path, d=8.0):
    e = "0.9*sin(2*PI*55*t)*exp(-28*mod(t,0.968))+0.6*sin(2*PI*48*t)*exp(-30*mod(t-0.24,0.968))*gte(mod(t,0.968),0.24)"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"aevalsrc='{e}':s=48000:d={d}", "-af", "lowpass=f=140,volume=2.2", "-ac", "2", str(path)])

def audio(plan, tmp, out):
    ev = []
    for c in plan["cuts"]:
        t0 = c["at"]
        if c.get("take_audio"): ev.append((ROOT / c["take_audio"], t0 - c["piece"][0] if c["piece"][0] else t0, 1.0, c["piece"][0], c["dur"] + 0.6))
        for l in c.get("lines", []): ev.append((ROOT / l["file"], t0 + l["at"], 1.0, 0, None))
        for x in c.get("sfx", []):
            if x.get("synth") == "heartbeat":
                hb = tmp / "heartbeat.wav"; heartbeat(hb) if not hb.exists() else None; ev.append((hb, t0 + x.get("at", 0), 0.9, 0, c["dur"]))
            else: ev.append((ROOT / x["file"], t0 + x["at"], x.get("gain", 1.0), 0, None))
    total = plan["cuts"][-1]["at"] + plan["cuts"][-1]["dur"]
    ins, fl = [], []
    for i, (f, at, g, skip, maxd) in enumerate(ev):
        if not Path(f).exists(): print("MISSING audio", f); continue
        ins += ["-i", str(f)]; k = len(ins) // 2 - 1
        at = max(0.0, at); tr = f"atrim=start={skip}" + (f":duration={maxd}" if maxd else "")
        fl.append(f"[{k}]aformat=sample_rates=48000:channel_layouts=stereo,{tr},asetpts=PTS-STARTPTS,volume={g},adelay={int(at*1000)}|{int(at*1000)}[a{k}]")
    n = len(fl)
    fc = ";".join(fl) + ";" + "".join(f"[a{k}]" for k in range(n)) + f"amix=inputs={n}:normalize=0:duration=longest,atrim=0:{total:.3f},loudnorm=I=-16:TP=-1.5:LRA=11[out]"
    run(["ffmpeg", "-y", "-loglevel", "error"] + ins + ["-filter_complex", fc, "-map", "[out]", "-ar", "48000", str(out)])

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--plan", default=str(OT / "shots" / "ep01_v5_dramabox_plan.json"))
    ap.add_argument("--v4", default=str(OT / "pulled" / "ep1_v4_main")); ap.add_argument("--v5", default=str(OT / "pulled" / "ep1_v5_inserts"))
    ap.add_argument("--out", default=str(OT / "episode" / "tlm_ep01_v5_720x1280.mp4")); ap.add_argument("--allow-fail", action="store_true")
    ap.add_argument("--placeholders", action="store_true"); ap.add_argument("--tmp", default="/tmp/asm_v5")
    ap.add_argument("--src", action="append", default=[], help="extra source dir NAME=DIR for cuts 'NAME:<id>' (ep2: e2=pulled/ep2_main/out)")
    a = ap.parse_args(); plan = json.loads(Path(a.plan).read_text()); tmp = Path(a.tmp); shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
    fontdir = {"sans": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"}
    report = []; segs = []
    for c in plan["cuts"]:
        seg = tmp / f"seg_{c['n']:02d}.mkv"
        if c["source"] == "card:time": card(c, seg)
        else:
            src = ROOT / c["file"] if c["source"].startswith("keep:") else resolve(c["source"], {"v4": a.v4, "v5": a.v5, **dict(x.split("=", 1) for x in a.src)}, a.allow_fail, a.placeholders, tmp, report)
            segment(c, src, seg, fontdir)
        segs.append(seg)
    (tmp / "concat.txt").write_text("".join(f"file '{s}'\n" for s in segs))
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "concat.txt"), "-c", "copy", str(tmp / "raw.mkv")])
    audio(plan, tmp, tmp / "mix.wav"); ass(plan, tmp / "ep.ass")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp / "raw.mkv"), "-i", str(tmp / "mix.wav"), "-vf", f"ass={tmp / 'ep.ass'}:fontsdir=/usr/share/fonts/truetype/sand-box/google",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", a.out])
    ph = [r for r in report if r[1] == "PLACEHOLDER"]
    rep = {"out": a.out, "duration": dur_of(a.out), "cuts": len(segs), "sources": report, "placeholders": len(ph), "shippable": not ph}
    Path(a.out).with_suffix(".report.json").write_text(json.dumps(rep, indent=1)); print(json.dumps({k: rep[k] for k in ("out", "duration", "cuts", "placeholders", "shippable")}))
    shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
