"""SatrCut v2 PC runner: render -> QC gate -> automatic retake, per shot, until a take passes or max_tries.
Runs on the GPU host with ComfyUI's embedded python (same as runner_shadow.py, which stays untouched):
  ...\\python_embeded\\python.exe runner_v2.py <job_dir> [--max-tries 4] [--fake <dir>]
job.json = {"shots": [merged shot incl. prompt, model, seeds, qc (gate spec), anchor]}; job_dir/start_<id>.png [end_<id>.png]
Per shot:
  attempt k: seed = shot.seeds[k] or seeds[0] + 1009*k
    - remedies after a FAIL (written to the log): HAND -> start/end re-cropped 12% tighter from the bottom;
      STILL/KEN_BURNS -> pin replaced by a drift end frame; FACES_CLOSE/FACE_LOST/MOUTHING/IDENTITY -> new seed only
    - ComfyUI crash/hang (INFRA) -> restart ComfyUI and retry the same seed once
    - STOP file / SATRCUT_STOP_AT are checked before EVERY attempt; never deletes STOP
  gate PASS / TRIM a-b (clean window long enough) / REVIEW (hand inserts) ends the loop; the take is written to
  out/<id>/manifest.json with all attempts and their gate reports; every rejection goes to job_dir/gate_log.jsonl.
--fake <dir>: no GPU; "renders" copy <dir>/<id>_<k>.mp4 (attempt k) so the retry logic can be tested on the box."""
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import keyframe_ops as ko

R = Path(os.environ.get("SATRCUT_ROOT", str(Path.home() / "satrcut")))
QCPY = Path(os.environ.get("SATRCUT_QC_PY", str(R / "qcvenv" / "Scripts" / "python.exe")))
GATE = HERE / "qc_gate.py"
EST_MIN = {"5b": 15, "a14b": 22, "infinitetalk": 28}

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def gate(mp4, spec, out_json):
    refs = [f"{k}={v}" for k, v in (spec.get("refs") or {}).items()]
    spec_file = Path(out_json).with_suffix(".spec.json"); spec_file.write_text(json.dumps({k: v for k, v in spec.items() if k != "refs"}))
    cmd = [str(QCPY), str(GATE), str(mp4), "--spec", str(spec_file), "--out", str(out_json)] + (["--refs"] + refs if refs else [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if not Path(out_json).exists(): return {"verdict": "FAIL", "reasons": [f"GATE_ERROR {r.stderr[-400:]}"]}
    rep = json.loads(Path(out_json).read_text()); rep.pop("per_frame", None); return rep

def remedy(rep, shot, job_dir, k):
    """Change the inputs for the next attempt based on what failed. Returns a note for the log."""
    rs = " ".join(rep.get("reasons", [])); sid = shot["id"]; notes = []
    st, en = job_dir / f"start_{sid}.png", job_dir / f"end_{sid}.png"
    if "HAND" in rs and not shot.get("allow_hands") and shot.get("_crops", 0) < 2:
        for f in (st, en):
            if f.exists(): shutil.copy(f, f.with_name(f.stem + f"_pre_crop{shot.get('_crops', 0)}.png")); ko.crop_tighter(f, f)
        shot["_crops"] = shot.get("_crops", 0) + 1; notes.append("start/end cropped 12% tighter (hands)")
    if ("STILL" in rs or "KEN_BURNS" in rs) and shot.get("anchor", "pin") == "pin" and st.exists():
        ko.drift_end(st, en); shot["anchor"] = "drift"; notes.append("pin -> drift end frame (too still)")
    return "; ".join(notes) or "new seed only"

def render_one(shot, seed, sd, job_dir, fake, k):
    if fake:
        src = Path(fake) / f"{shot['id']}_{k}.mp4"
        if not src.exists(): raise RuntimeError(f"fake render missing {src}")
        d = sd / f"wan_s{seed}"; d.mkdir(parents=True, exist_ok=True); dst = d / f"wan_{shot['id']}_s{seed}.mp4"; shutil.copy(src, dst)
        return {"mp4": str(dst), "render_s": 0.0}
    sys.path.insert(0, str(R / "own-tool" / "pipeline"))
    import wan_comfy, wan14b_comfy
    mod = wan14b_comfy if shot.get("model") == "a14b" else wan_comfy
    return mod.render(R / "ComfyUI_windows_portable" / "ComfyUI", 8188, shot, seed, sd / f"wan_s{seed}", job_dir / f"start_{shot['id']}.png", timeout_s=45 * 60)

def comfy_up(fake):
    if fake: return None
    import urllib.request
    sys.path.insert(0, str(R / "own-tool" / "pipeline")); import wan_comfy
    try: urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=2); return None
    except Exception: return wan_comfy.start_server(R / "ComfyUI_windows_portable" / "ComfyUI", 0, 8188, R / "jobs" / "comfyui_v2.log", extra=("--cache-none",))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("job_dir"); ap.add_argument("--max-tries", type=int, default=4); ap.add_argument("--fake")
    a = ap.parse_args(); job_dir = Path(a.job_dir); job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    out = job_dir / "out"; out.mkdir(exist_ok=True); glog = job_dir / "gate_log.jsonl"
    stop_at = float(os.environ.get("SATRCUT_STOP_AT", "0") or 0); stop_file = job_dir.parent / "STOP"
    server = comfy_up(a.fake)
    for shot in job["shots"]:
        sid = shot["id"]; sd = out / sid
        if (sd / "manifest.json").exists(): log("SKIP done", sid); continue
        sd.mkdir(parents=True, exist_ok=True); spec = dict(shot.get("qc") or {}); spec.setdefault("need_s", shot.get("need_s"))
        seeds = list(shot.get("seeds") or [101]); attempts = []; chosen = None
        for k in range(shot.get("max_tries", a.max_tries)):
            if stop_file.exists(): log("STOP file present; not starting", sid, "attempt", k); break
            need = EST_MIN.get(shot.get("model", "a14b"), 22) * 60
            if stop_at and time.time() + need > stop_at: log("DEADLINE (SATRCUT_STOP_AT): not starting", sid, "attempt", k); break
            seed = seeds[k] if k < len(seeds) else seeds[0] + 1009 * k
            t0 = time.time()
            try:
                r = render_one(shot, seed, sd, job_dir, a.fake, k)
            except Exception as e:   # INFRA: ComfyUI crash / hostbuffer / access violation -> restart, same seed once
                log("INFRA", sid, seed, repr(e)[:300])
                with open(glog, "a") as f: f.write(json.dumps({"t": time.strftime("%F %T"), "shot": sid, "seed": seed, "cause": "INFRA", "error": repr(e)[:500]}) + "\n")
                if server is not None: server.kill()
                server = comfy_up(a.fake)
                try: r = render_one(shot, seed, sd, job_dir, a.fake, k)
                except Exception as e2: attempts.append({"seed": seed, "infra_error": repr(e2)[:300]}); continue
            rep = gate(r["mp4"], spec, sd / f"gate_s{seed}.json")
            att = {"seed": seed, "mp4": r["mp4"], "render_s": r.get("render_s"), "gate_s": round(time.time() - t0 - (r.get("render_s") or 0), 1),
                   "verdict": rep["verdict"], "reasons": rep.get("reasons", []), "clean_window": rep.get("clean_window")}
            attempts.append(att); log(sid, "seed", seed, rep["verdict"], "; ".join(rep.get("reasons", []))[:200])
            if rep["verdict"] in ("PASS", "TRIM", "REVIEW"):
                chosen = att; break
            note = remedy(rep, shot, job_dir, k); att["remedy_next"] = note
            with open(glog, "a") as f: f.write(json.dumps({"t": time.strftime("%F %T"), "shot": sid, "seed": seed, "cause": "GATE", "reasons": rep.get("reasons"), "clean_window": rep.get("clean_window"), "remedy": note}) + "\n")
        man = {"shot": shot, "backend": "shadow-v2", "attempts": attempts, "chosen": chosen,
               "status": ("accepted_by_gate" if chosen and chosen["verdict"] in ("PASS", "TRIM") else "needs_review" if chosen else "all_failed"),
               "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if chosen or len(attempts) >= shot.get("max_tries", a.max_tries):   # unfinished (STOP/deadline) shots stay resumable
            (sd / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding="utf-8")
        log("SHOT", sid, man["status"], f"{len(attempts)} attempt(s)")
        if stop_file.exists() or (stop_at and time.time() > stop_at - 60): break
    if server is not None:
        server.terminate()
    log("JOB DONE", job_dir)

if __name__ == "__main__":
    main()
