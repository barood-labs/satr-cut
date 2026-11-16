"""SatrCut own-tool: run shots from the shot-list JSON, shot by shot.

  python run_shots.py --shots ../shots/ep01_own_tool_shots.json --ids 4-06 \
      --work ./work --out ./out

Stages per shot: (1) Wan2.2-TI2V-5B image-to-video from the locked start frame,
(2) MuseTalk 1.5 lip-sync to the padded dialogue take, (3) mux + 480x848 conform
+ burned-subtitle preview + QC sheets. Writes manifest.json per shot.
--stages lets you resume (e.g. --stages lipsync,finish --wan-mp4 path).
"""
import argparse, json, os, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import log, load_shots, sha256
import finish

def repair_seconds(comfy, shot, wan_mp4, sd, wan_comfy):
    """Re-render [from_s, to_s) starting from the frame at from_s and splice it in."""
    import subprocess
    fps = shot["fps"]
    frames_dir = sd / "repair_src"; frames_dir.mkdir(exist_ok=True)
    os.system(f"ffmpeg -y -v error -i '{wan_mp4}' '{frames_dir}/%05d.png'")
    files = sorted(frames_dir.glob("*.png"))
    for k, rp in enumerate(shot["repairs"]):
        a, b = int(round(rp["from_s"] * fps)), min(len(files), int(round(rp["to_s"] * fps)))
        n = b - a + 1
        n = n + (1 - n % 4) % 4  # 4n+1
        sub = dict(shot, frames=n)
        r = wan_comfy.render(comfy, 8188, sub, rp.get("seed", 777 + k), sd / f"repair_{k}", files[a])
        new = sorted(Path(r["frames_dir"]).glob("*.png"))
        for i in range(a, b):
            shutil.copy(new[i - a], files[i])
        # 3-frame crossfade back into the original at b
        from PIL import Image
        for j in range(3):
            if b + j < len(files) and (b - a + j) < len(new):
                w = (j + 1) / 4
                im = Image.blend(Image.open(new[b - a + j]).convert("RGB"), Image.open(files[b + j]).convert("RGB"), w)
                im.save(files[b + j])
    out = sd / f"wan_{shot['id']}_repaired.mp4"
    os.system(f"ffmpeg -y -v error -framerate {fps} -i '{frames_dir}/%05d.png' -c:v libx264 -crf 12 -pix_fmt yuv420p '{out}'")
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", required=True)
    ap.add_argument("--ids", default="")
    ap.add_argument("--base", default=None, help="folder that shot paths are relative to (default: shots file's parent's parent)")
    ap.add_argument("--work", default="./work")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--stages", default="wan,lipsync,finish")
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--wan-mp4", default=None)
    ap.add_argument("--verify-sha", action="store_true")
    a = ap.parse_args()
    base = Path(a.base) if a.base else Path(a.shots).resolve().parent.parent
    work, out = Path(a.work), Path(a.out)
    work.mkdir(parents=True, exist_ok=True); out.mkdir(parents=True, exist_ok=True)
    stages = set(a.stages.split(","))
    shots = load_shots(a.shots)
    if a.ids:
        want = set(a.ids.split(","))
        shots = [s for s in shots if s["id"] in want]
    comfy = mt = py = server = None
    wan_comfy = None
    if "wan" in stages or "repair" in stages:
        import wan_comfy
        t = time.time(); comfy = wan_comfy.setup(work, verify_sha=a.verify_sha); setup_wan = time.time() - t
        server = wan_comfy.start_server(comfy, a.gpu, 8188, out / "comfyui.log")
    if "lipsync" in stages:
        import lipsync_musetalk
        t = time.time(); mt, py = lipsync_musetalk.setup(work); setup_lip = time.time() - t
    prev_last = None
    for shot in shots:
        sd = out / shot["id"]; sd.mkdir(exist_ok=True)
        man = {"shot": shot, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "stages": {}}
        if shot["start_frame"] == "@prev_last":
            # rule: the previous shot's last still is this shot's reference
            if prev_last is None:
                raise ValueError(f"{shot['id']}: @prev_last but no previous shot rendered in this run")
            start_png = prev_last
        else:
            start_png = base / shot["start_frame"]
        man["start_frame_sha256"] = sha256(start_png)
        dur = shot["frames"] / shot["fps"]
        padded = finish.pad_audio(base / shot["audio"], sd / f"audio_{shot['id']}_padded.wav", shot["audio_offset_s"], dur)
        wan_mp4 = Path(a.wan_mp4) if a.wan_mp4 else None
        if "wan" in stages:
            if "wan_setup_s" not in man: man["wan_setup_s"] = round(setup_wan, 1)
            for seed in shot["seeds"]:
                r = wan_comfy.render(comfy, 8188, shot, seed, sd / f"wan_s{seed}", start_png)
                man["stages"].setdefault("wan", []).append(r)
                wan_mp4 = wan_mp4 or Path(r["mp4"])
                finish.contact_sheet(r["mp4"], sd / f"qc_wan_s{seed}_sheet.png")
        if "repair" in stages and shot.get("repairs"):
            # rule: regenerate only the broken seconds, splice them back
            wan_mp4 = repair_seconds(comfy, shot, wan_mp4, sd, wan_comfy)
            man["stages"]["repair"] = {"repairs": shot["repairs"], "mp4": str(wan_mp4)}
        if "lipsync" in stages:
            import lipsync_musetalk
            man["lipsync_setup_s"] = round(setup_lip, 1)
            r = lipsync_musetalk.run(mt, py, shot, wan_mp4.resolve(), padded.resolve(), sd / "lipsync", gpu=a.gpu)
            man["stages"]["lipsync"] = r
            lip_mp4 = Path(r["mp4"])
            # stage 2b: keep MuseTalk only on the lower face, restore skin detail, rebuild broken frames
            import postfix_lipsync
            for name, v in (("src_frames", wan_mp4), ("lip_frames", lip_mp4)):
                (sd / name).mkdir(exist_ok=True)
                os.system(f"ffmpeg -y -v error -i '{v}' '{sd / name}/%05d.png'")
            rep = postfix_lipsync.run(sd / "src_frames", sd / "lip_frames", sd / "lipsync" / "face_track.json", sd / "postfix_frames")
            pf = sd / f"postfix_{shot['id']}.mp4"
            os.system(f"ffmpeg -y -v error -framerate {shot['fps']} -i '{sd}/postfix_frames/%05d.png' -c:v libx264 -crf 12 -pix_fmt yuv420p '{pf}'")
            man["stages"]["postfix"] = {"mp4": str(pf), "rebuilt": rep["broken_frames_rebuilt"]}
            lip_mp4 = pf
        else:
            lip_mp4 = wan_mp4
        if "finish" in stages:
            master = finish.mux(lip_mp4, padded, sd / f"tlm_{shot['id']}_owntool_master_{shot['width']}x{shot['height']}.mp4")
            conf = finish.conform_480x848(master, sd / f"tlm_{shot['id']}_owntool_480x848_LOSSLESS.mkv")
            sub = finish.burn_subtitle(master, shot.get("subtitle", ""), sd / f"review_{shot['id']}_subbed_9x16.mp4",
                                       start_s=shot["audio_offset_s"], end_s=dur)
            finish.contact_sheet(master, sd / f"qc_final_sheet_0.25s.png")
            tr = sd / "lipsync" / "face_track.json"
            if tr.exists():
                fdir = sd / "qc_final_frames"; fdir.mkdir(exist_ok=True)
                os.system(f"ffmpeg -y -v error -i '{master}' '{fdir}/%05d.png'")
                finish.mouth_strip(fdir, tr, sd / "qc_mouth_strip.png")
            man["stages"]["finish"] = {"master": finish.describe(master), "conform": str(conf),
                                       "subbed_preview": str(sub) if sub else None}
        if wan_mp4 is not None:
            last = sd / f"{shot['id']}_last_frame.png"
            os.system(f"ffmpeg -y -v error -sseof -0.05 -i '{wan_mp4}' -frames:v 1 -update 1 '{last}'")
            prev_last = last if last.exists() else None
        man["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        (sd / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False))
        log("DONE", shot["id"], json.dumps(man["stages"], ensure_ascii=False)[:800])
    if server is not None:
        server.terminate()

if __name__ == "__main__":
    main()
