"""Shared helpers for the SatrCut own-tool pipeline (Kaggle T4 or any CUDA box)."""
import hashlib, json, os, subprocess, sys, time, urllib.request
from pathlib import Path

LOG_T0 = time.time()

def log(*a):
    print(f"[{time.time()-LOG_T0:7.1f}s]", *a, flush=True)

def sh(cmd, check=True, env=None, cwd=None):
    log("$", cmd if isinstance(cmd, str) else " ".join(map(str, cmd)))
    r = subprocess.run(cmd, shell=isinstance(cmd, str), env=env, cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {cmd}")
    return r.returncode

def sha256(path, bufsize=1 << 24):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def hf_download(repo, revision, remote_path, dest, expect_sha=None, expect_size=None):
    """Download one file from the HF hub with resume; verify size/sha when given."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (expect_size is None or dest.stat().st_size == expect_size):
        if expect_sha is None or sha256(dest) == expect_sha:
            log("cached", dest.name)
            return dest
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{remote_path}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    # aria2c is much faster on Kaggle; fall back to curl
    if subprocess.run("command -v aria2c", shell=True, capture_output=True).returncode == 0:
        sh(["aria2c", "-q", "-x", "16", "-s", "16", "-c", "-d", str(tmp.parent), "-o", tmp.name, url])
    else:
        sh(["curl", "-L", "--fail", "--retry", "5", "-C", "-", "-o", str(tmp), url])
    if expect_size is not None and tmp.stat().st_size != expect_size:
        raise RuntimeError(f"size mismatch {dest.name}: {tmp.stat().st_size} != {expect_size}")
    if expect_sha is not None:
        got = sha256(tmp)
        if got != expect_sha:
            raise RuntimeError(f"sha256 mismatch {dest.name}: {got}")
    tmp.rename(dest)
    log("downloaded", dest.name, dest.stat().st_size)
    return dest

def load_shots(path):
    d = json.loads(Path(path).read_text())
    out = []
    for s in d["shots"]:
        m = json.loads(json.dumps(d["defaults"]))
        lip = m.pop("lipsync")
        m.update({k: v for k, v in s.items() if k != "lipsync"})
        lip.update(s.get("lipsync", {}))
        m["lipsync"] = lip
        out.append(m)
    return out

def ffprobe_json(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True)
    return json.loads(r.stdout or "{}")


# ---------- prompt builder + shot rules (Andy Lo discipline) ----------
def build_prompt(doc, s):
    chars = doc.get("characters", {})
    who = []
    for n in s["in_frame"]:
        c = chars[n]
        who.append(f"{n.split(' (')[0]}: {c['look']}, with {c['unique_detail']}")
    place = doc.get("places", {}).get(s.get("place"), {}).get("desc", s.get("place", ""))
    names = [n.split(" (")[0] for n in s["in_frame"]]
    in_frame = (", ".join(names[:-1]) + " and " + names[-1]) if len(names) > 1 else (names[0] if names else "no people")
    parts = [doc["film_look"], s.get("camera", ""), f"Place: {place}. {s.get('setting', '')}",
             ("Same people as the start image. " + " ".join(w + "." for w in who)) if who else "Same place and objects as the start image.",
             f"In frame: {in_frame}, and nobody else.", "Action: " + s["action"]]
    # Only an on-screen speaker with a real audio take gets a quoted line (2026-11-10: names[0] put Sam's/Riley's
    # lines in Alex's mouth on 2-03/5-03 and a quoted shout rendered as garbled on-screen text).
    spk_raw = s.get("speaker") or ""; spk = spk_raw.split(" (")[0].strip().title()
    if s.get("line") and s.get("audio") and spk in names and "O.S." not in spk_raw and "V.O." not in spk_raw:
        parts.append(f'{spk} says: "{s["line"]}" ({s.get("line_delivery", "spoken in English exactly as written")}).')
    parts.append("Same faces and clothes for the whole shot. One continuous shot, no cuts.")
    return " ".join(p.strip() for p in parts if p.strip())

def lint_shot(doc, s):
    errs = []
    if s["frames"] % 4 != 1: errs.append("frames must be 4n+1 for Wan")
    if s["frames"] / s["fps"] > 5.1: errs.append("one length per shot: keep <= 5 s; split longer beats")
    if s["action"].count(". ") > 0: errs.append("one action per shot: action must be one sentence")
    for n in s["in_frame"]:
        if n not in doc.get("characters", {}): errs.append(f"no reference sheet for {n}")
    if "One continuous shot, no cuts." not in s["prompt"]: errs.append("prompt missing 'One continuous shot, no cuts.'")
    if "nobody else" not in s["prompt"]: errs.append("prompt missing 'and nobody else'")
    return errs

_load_shots_basic = load_shots
def load_shots(path):
    doc = json.loads(Path(path).read_text())
    out = []
    for raw, m in zip(doc["shots"], _load_shots_basic(path)):
        if "prompt" not in raw:
            m["prompt"] = build_prompt(doc, m)
        errs = lint_shot(doc, m)
        if errs:
            raise ValueError(f"shot {m['id']} breaks rules: {errs}")
        out.append(m)
    return out
