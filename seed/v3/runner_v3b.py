"""runner_v3b (ep2+, 2026-11-14 17:30 MT) = runner_v3 + (a) Wan2.2-TI2V-5B shots (shot.model == "5b": start frame only,
  preset fields steps/cfg/shift/sampler/scheduler via pipeline/wan_comfy.py), (b) a RENDER_ERROR (PC reboot / ComfyUI died)
  does NOT burn a try: the shot is parked as partial.json and the runner exits 3 so the starter relaunches it,
  (c) est_min looked up by exact "preset:frames" first. runner_v3.py (ep1 jobs) is untouched.
Ep1 v4 motion batch runner (2026-11-14). Runs on the GPU host with ComfyUI's embedded python:
  python_embeded\\python.exe -X utf8 -u own-tool\\v3\\runner_v3.py <job_dir>
Per shot: render (Wan2.2 A14B first+last frame, motion preset) -> gate_v3 (motion_gate stillness rules calibrated on the
v3 review's flagged shots + dissolve/soft/noisy/cut sanity, run in mtvenv python which has cv2) -> on FAIL re-render with
the NEXT STRONGER preset and a new seed (max_tries) -> manifest.json with the chosen take (PASS, else best score marked FAIL_ALL).
job.json: {shots:[...kind, presets?, seeds], presets:{name:{a14b fields}}, ladder:[...], preset_choice: path, default_preset,
           max_tries, est_min:{"preset:frames": min}, mode: "compare" (settings test: run every shot, write preset_choice.json)}
Safety (never relaxed): checks jobs\\STOP before EVERY attempt and halts (never deletes it); halts if jobs\\MAINT_WINDOW is
created/updated after this runner started; never STARTS an attempt whose estimate overlaps 00:30-04:35 MT (07:30-11:35 UTC)
on any day or SATRCUT_STOP_AT; halts below 3 GB free on C:. Deletes only its own PNG frame dirs after encoding."""
import json, os, shutil, subprocess, sys, time, urllib.request, zipfile, datetime as dt
from pathlib import Path
R = Path(os.environ.get("SATRCUT_ROOT", str(Path.home() / "satrcut"))); OT = R / "own-tool"; JOBS = R / "jobs"
sys.path.insert(0, str(OT / "pipeline"))
os.environ["PATH"] = f"{R / 'tools' / 'ffmpeg' / 'bin'};{os.environ['PATH']}"
from common import log
import wan_comfy, wan14b_comfy
COMFY = R / "ComfyUI_windows_portable" / "ComfyUI"
MTPY = R / "mtvenv" / "Scripts" / "python.exe"; GATE = OT / "v3" / "gate_v3.py"
UTC = dt.timezone.utc; T0 = time.time()

def up(port=8188):
    try: urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=2); return True
    except Exception: return False

def overlaps_window(t0, t1):
    d = dt.datetime.fromtimestamp(t0, UTC).date() - dt.timedelta(days=1)
    for k in range(4):
        dd = d + dt.timedelta(days=k)
        a = dt.datetime(dd.year, dd.month, dd.day, 7, 30, tzinfo=UTC).timestamp(); b = dt.datetime(dd.year, dd.month, dd.day, 11, 35, tzinfo=UTC).timestamp()
        if t0 < b and t1 > a: return True
    return False

def halt_reason(need_s):
    if (JOBS / "STOP").exists(): return "STOP file present"
    sw = JOBS / "MAINT_WINDOW"
    if sw.exists() and sw.stat().st_mtime > T0 - 30: return "MAINT_WINDOW created/updated after start"
    now = time.time()
    if overlaps_window(now, now + need_s): return f"attempt ({need_s/60:.0f} min) would overlap 00:30-04:35 MT"
    stop_at = float(os.environ.get("SATRCUT_STOP_AT", "0") or 0)
    if stop_at and now + need_s > stop_at: return "SATRCUT_STOP_AT"
    if shutil.disk_usage("C:\\").free < 3e9: return "C: below 3 GB free"
    return None

def est_s(job, preset, shot):
    em = job.get("est_min", {}); exact = em.get(f"{preset}:{shot['frames']}")
    if exact: return (exact + 1) * 60 * 1.15
    base = em.get(f"{preset}:81", 30)
    scale = shot["width"] * shot["height"] * shot["frames"] / (720 * 1280 * 81)
    return (base * scale + (3 if scale < 0.9 else 1)) * 60 * 1.15   # +15 % margin

def gate(mp4, kind, out_json):
    try:
        p = subprocess.run([str(MTPY), str(GATE), str(mp4), "--kind", kind, "--json", str(out_json)], capture_output=True, text=True, timeout=900)
        return json.loads(Path(out_json).read_text())
    except Exception as e:
        return {"verdict": "GATE_ERROR", "reasons": [repr(e)[:300]], "score": 0}

def attempts_for(job, shot, chosen):
    if shot.get("presets"): return shot["presets"]
    lad = job["ladder"]; i = lad.index(chosen) if chosen in lad else 1
    seq = [lad[min(i + k, len(lad) - 1)] for k in range(job.get("max_tries", 2))]
    return seq

def status(job_dir, **kw):
    p = job_dir / "status.json"; s = json.loads(p.read_text()) if p.exists() else {}
    s.update(kw, updated=dt.datetime.now(UTC).isoformat(timespec="seconds")); p.write_text(json.dumps(s, indent=1))

def main(job_dir):
    job_dir = Path(job_dir); job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    out = job_dir / "out"; out.mkdir(exist_ok=True); glog = job_dir / "gate_log.jsonl"
    chosen = job.get("default_preset", "hybrid8")
    pc = job.get("preset_choice")
    if pc and (job_dir / pc).exists():
        try: chosen = json.loads((job_dir / pc).read_text())["preset"]; log("preset from settings test:", chosen)
        except Exception as e: log("preset_choice unreadable", e)
    status(job_dir, preset=chosen, state="running")
    server = None; halted = None; results = []
    try:
        for shot in job["shots"]:
            sid = shot["id"]; sd = out / sid
            if (sd / "manifest.json").exists(): log("SKIP done", sid); continue
            sd.mkdir(parents=True, exist_ok=True); start_png = job_dir / f"start_{sid}.png"
            man = {"shot": {k: v for k, v in shot.items()}, "backend": "shadow-v3", "started": dt.datetime.now(UTC).isoformat(timespec="seconds"), "attempts": []}
            seq = attempts_for(job, shot, chosen)
            # resume (12:36 MT fix): PC restarts re-ran attempt 1 with the same seed 4x (1-01m); reuse finished attempts from gate_log
            prior = {}
            if glog.exists():
                for ln in glog.read_text(encoding="utf-8").splitlines():
                    try: rr = json.loads(ln)
                    except Exception: continue
                    if rr.get("shot") == sid and rr.get("mp4") and (job_dir / rr["mp4"]).exists(): prior[(rr["attempt"], rr["preset"])] = rr
            for i, preset in enumerate(seq):
                old = prior.get((i + 1, preset))
                if old:
                    man["attempts"].append(old); log("RESUME reuse", sid, "a%d" % (i + 1), preset, old.get("verdict"))
                    if old.get("verdict") == "PASS" and job.get("mode") != "compare": break
                    continue
                need = est_s(job, preset, shot); why = halt_reason(need)
                if why: halted = f"{sid} attempt {i+1}: {why}"; log("HALT", halted); break
                if not up():
                    server = wan_comfy.start_server(COMFY, 0, 8188, job_dir / "comfyui.log", extra=("--cache-none",))
                seed = shot["seeds"][i % len(shot["seeds"])]
                rs = dict(shot); rs["a14b"] = dict(job["presets"][preset])
                if shot.get("model") == "5b": rs.update(job["presets"][preset])
                ad = sd / f"a{i+1}_{preset}_s{seed}"; status(job_dir, current=f"{sid} a{i+1} {preset} s{seed}", eta_attempt_min=round(need / 60))
                t = time.time(); rec = {"shot": sid, "attempt": i + 1, "preset": preset, "seed": seed, "a14b": rs["a14b"], "t_start": dt.datetime.now(UTC).isoformat(timespec="seconds")}
                try:
                    if shot.get("model") == "5b": r = wan_comfy.render(COMFY, 8188, rs, seed, ad, start_png)
                    else: r = wan14b_comfy.render(COMFY, 8188, rs, seed, ad, start_png)
                    shutil.rmtree(r["frames_dir"], ignore_errors=True)          # our own PNG intermediates (mp4 kept)
                    g = gate(r["mp4"], shot.get("kind", "character"), ad / "gate.json")
                    rec.update(mp4=os.path.relpath(r["mp4"], job_dir), render_s=r["render_s"], verdict=g.get("verdict"), reasons=g.get("reasons", []),
                               score=g.get("score"), subject_med=g.get("subject_med"), area_med=g.get("area_med"), change=g.get("change"),
                               sanity={k: g.get("sanity", {}).get(k) for k in ("sharp_ratio", "noise_ratio", "jump_ratio")})
                except Exception as e:
                    rec.update(verdict="RENDER_ERROR", reasons=[repr(e)[:500]], render_s=round(time.time() - t, 1))
                    if server is not None and server.poll() is not None: server = None
                rec["wall_s"] = round(time.time() - t, 1)
                with open(glog, "a", encoding="utf-8") as f: f.write(json.dumps(rec) + "\n")
                nerr = sum(1 for ln in glog.read_text(encoding="utf-8").splitlines() if '"RENDER_ERROR"' in ln and f'"shot": "{sid}"' in ln and f'"attempt": {i + 1},' in ln)
                if rec.get("verdict") == "RENDER_ERROR" and nerr < 3:      # reboot / ComfyUI crash: retry this same attempt on the next launch (max 3x)
                    halted = f"{sid} attempt {i+1}: RENDER_ERROR (not counted; retried next launch)"; log("HALT", halted); break
                man["attempts"].append(rec)
                log("ATTEMPT", sid, preset, seed, rec.get("verdict"), rec.get("score"), "; ".join(rec.get("reasons", []))[:200])
                if rec.get("verdict") == "PASS" and job.get("mode") != "compare": break
            if halted and not man["attempts"]: break
            if halted and job.get("mode") != "compare" and not any(a.get("verdict") == "PASS" for a in man["attempts"]):
                (sd / "partial.json").write_text(json.dumps(man, indent=1)); break      # resume this shot next run
            ok = [a for a in man["attempts"] if a.get("mp4")]
            passed = [a for a in ok if a.get("verdict") == "PASS"]
            pick = passed[0] if passed else (max(ok, key=lambda a: a.get("score") or 0) if ok else None)
            man.update(chosen=pick, status="PASS" if passed else ("FAIL_ALL" if ok else "ERROR"), finished=dt.datetime.now(UTC).isoformat(timespec="seconds"))
            (sd / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding="utf-8")
            results.append(man); status(job_dir, last_done=sid, last_status=man["status"])
            if halted: break
    finally:
        if server is not None:
            server.terminate()
            try: server.wait(60)
            except Exception: pass
    if job.get("mode") == "compare":
        choose(job_dir, job)
    zp = job_dir / "satrcut-output.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in out.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".mp4", ".json") and f.stat().st_size < 90e6: z.write(f, f.relative_to(out))
        for extra in ("gate_log.jsonl", "status.json", "preset_choice.json"):
            if (job_dir / extra).exists(): z.write(job_dir / extra, extra)
    status(job_dir, state="halted: " + halted if halted else "done", zip=str(zp))
    log("ZIP", zp, zp.stat().st_size, "halted" if halted else "done")
    sys.exit(3 if halted else 0)

def choose(job_dir, job):
    """Settings test: most motion among takes that pass the sanity checks (no DISSOLVE/SOFT/NOISY/CUT/error);
    a cheaper preset wins when its score is within 10 % of the best."""
    rows = [json.loads(l) for l in (job_dir / "gate_log.jsonl").read_text().splitlines() if l.strip()] if (job_dir / "gate_log.jsonl").exists() else []
    cost = {p: i for i, p in enumerate(job["ladder"])}
    def sane(r): return r.get("mp4") and not any(w in x for x in r.get("reasons", []) for w in ("DISSOLVE", "SOFT", "NOISY", "CUT", "error", "ERROR"))
    ok = sorted([r for r in rows if sane(r)], key=lambda r: cost.get(r["preset"], 9))
    pick = None
    if ok:
        best = max(r["score"] or 0 for r in ok)
        pick = next(r for r in ok if (r["score"] or 0) >= 0.9 * best)
    preset = pick["preset"] if pick else job.get("default_preset", "hybrid8")
    res = {"preset": preset, "why": "max motion among sane takes (cheaper wins within 10 %)" if pick else "no sane test take; default",
           "table": [{k: r.get(k) for k in ("preset", "verdict", "score", "subject_med", "area_med", "change", "render_s", "reasons", "sanity")} for r in rows],
           "written": dt.datetime.now(UTC).isoformat(timespec="seconds")}
    (job_dir / "preset_choice.json").write_text(json.dumps(res, indent=1)); log("PRESET CHOICE", preset)

if __name__ == "__main__":
    main(sys.argv[1])
