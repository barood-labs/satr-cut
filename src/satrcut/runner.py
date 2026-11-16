"""Crash-proof render loop.

A finished attempt is never rendered again. An in-flight attempt is retried
once with the same seed (the host rebooted), then the ladder moves on.
STOP and MAINT_WINDOW are never deleted. Checkpoints are atomic JSON plus a
SQLite index that can be rebuilt from those files.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from satrcut import keyframes as kf
from satrcut.errors import InfraError, SatrcutError, SimulatedCrash
from satrcut.lint import lint_plan
from satrcut.project import episode_plan_path, load_series
from satrcut.providers import video_provider
from satrcut.providers.base import RenderRequest, VideoProvider
from satrcut.qc import PASSING, evaluate
from satrcut.speech import synthesize_plan
from satrcut.store import JobStore
from satrcut.util import atomic_write_json, free_gb, now_iso, read_json, sha256_file

QCFunc = Callable[..., Any]
DONE = set(PASSING)


def job_dir_for(project: Path, episode: int) -> Path:
    return project / "renders" / f"ep{episode:02d}"


def _say(message: str) -> None:
    print(message, flush=True)


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def _rel(project: Path, path: Path) -> str:
    return path.resolve().relative_to(project.resolve()).as_posix()


def _backoff() -> float:
    return float(os.environ.get("SATRCUT_RETRY_BACKOFF", "2"))


def _min_free_gb() -> float:
    return float(os.environ.get("SATRCUT_MIN_FREE_GB", "3"))


def _maint_overlaps(start: float, end: float) -> bool:
    spec = os.environ.get("SATRCUT_MAINT_UTC", "").strip()
    if not spec or "-" not in spec:
        return False
    left, right = spec.split("-", 1)
    try:
        sh, sm = (int(part) for part in left.split(":"))
        eh, em = (int(part) for part in right.split(":"))
    except ValueError:
        return False
    from datetime import datetime, timedelta, timezone

    day = datetime.fromtimestamp(start, timezone.utc).date()
    for delta in (-1, 0, 1):
        current = day + timedelta(days=delta)
        window_start = datetime(current.year, current.month, current.day, sh, sm, tzinfo=timezone.utc).timestamp()
        window_end = datetime(current.year, current.month, current.day, eh, em, tzinfo=timezone.utc).timestamp()
        if window_end <= window_start:
            window_end += 24 * 3600
        if start < window_end and end > window_start:
            return True
    return False


def halt_reason(project: Path, need_s: float, started: float) -> str | None:
    root = project / ".satrcut"
    if (root / "STOP").exists():
        return "STOP file present"
    window = root / "MAINT_WINDOW"
    if window.exists() and window.stat().st_mtime > started - 30:
        return "MAINT_WINDOW created or updated after start"
    now = time.time()
    stop_at = float(os.environ.get("SATRCUT_STOP_AT") or 0)
    if stop_at and now + need_s > stop_at:
        return "SATRCUT_STOP_AT"
    if _maint_overlaps(now, now + need_s):
        return "SATRCUT_MAINT_UTC"
    minimum = _min_free_gb()
    if minimum > 0 and free_gb(project) < minimum:
        return f"free disk under {minimum:g} GB"
    return None


def attempt_ladder(shot: dict[str, Any], plan: dict[str, Any], max_tries: int) -> list[dict[str, Any]]:
    seeds = [int(seed) for seed in (shot.get("seeds") or [611])]
    names = list(shot.get("presets") or plan.get("ladder") or ["hybrid8"])
    library = plan.get("presets") or {}
    steps = []
    for index in range(max_tries):
        name = names[min(index, len(names) - 1)]
        seed = seeds[index] if index < len(seeds) else seeds[-1] + 1009 * (index - len(seeds) + 1)
        steps.append({"seed": int(seed), "preset_name": name, "preset": dict(library.get(name) or {})})
    return steps


def remedy(report_reasons: list[str], shot: dict[str, Any], start: Path, end: Path, partial: dict[str, Any]) -> str:
    text = " ".join(report_reasons)
    notes: list[str] = []
    if "HAND" in text and not shot.get("allow_hands") and int(partial.get("crops") or 0) < 2:
        size = (int(shot.get("width") or 480), int(shot.get("height") or 848))
        for frame in (start, end):
            if frame.exists():
                backup = frame.with_name(frame.stem + f"_precrop{partial.get('crops', 0)}" + frame.suffix)
                shutil.copy(frame, backup)
                kf.crop_tighter(frame, frame, size=size)
        partial["crops"] = int(partial.get("crops") or 0) + 1
        notes.append("start/end cropped 12% tighter")
    anchor = partial.get("anchor") or shot.get("anchor") or "pin"
    if ("STILL" in text or "KEN_BURNS" in text) and anchor == "pin" and start.exists():
        kf.drift_end(start, end)
        partial["anchor"] = "drift"
        shot["anchor"] = "drift"
        notes.append("pin -> drift")
    return "; ".join(notes) or "new seed only"


def _render_once(provider: VideoProvider, request: RenderRequest, backoff: float) -> Any:
    """Render, and on an infra failure restart the server and retry the same seed once."""
    try:
        if not provider.healthy():
            provider.restart()
        return provider.render(request)
    except SimulatedCrash:
        raise
    except InfraError:
        time.sleep(backoff)
        provider.restart()
        return provider.render(request)


def _prepare(project: Path, plan: dict[str, Any], shot: dict[str, Any], shot_dir: Path) -> tuple[Path, Path]:
    kf.ensure_pair(project, shot)
    for role, field in (("start", "start_frame"), ("end", "end_frame")):
        kf.register(project, project / str(shot[field]), shot["id"], role, shot=shot, force=True)
    start = shot_dir / "start.png"
    end = shot_dir / "end.png"
    if not start.exists():
        shutil.copy(project / str(shot["start_frame"]), start)
    if not end.exists():
        shutil.copy(project / str(shot["end_frame"]), end)
    return start, end


def render_episode(
    project: Path,
    episode: int = 1,
    *,
    provider: VideoProvider | None = None,
    provider_name: str | None = None,
    max_tries: int = 4,
    qc_fn: QCFunc | None = None,
    skip_lint: bool = False,
    retry_failed: bool = False,
) -> int:
    project = project.resolve()
    series = load_series(project)
    plan_path = episode_plan_path(project, episode)
    if not plan_path.is_file():
        raise SatrcutError(f"no plan at {plan_path}. Run: satrcut plan generate -e {episode}")
    plan = read_json(plan_path)
    if not skip_lint:
        report = lint_plan(plan, project, episode=episode)
        if report.errors:
            _say(report.render())
            return 1
    provider = provider or video_provider(provider_name or (plan.get("providers") or {}).get("video") or series.providers.get("video") or "mock")
    qc_fn = qc_fn or evaluate
    backoff = _backoff()
    started = time.time()
    need_s = 20.0 if provider.name == "mock" else 22 * 60
    kf.ensure_sheets(project, plan)
    synthesize_plan(project, plan)
    folder = job_dir_for(project, episode)
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write_json(folder / "job.json", {"episode": episode, "provider": provider.name, "shots": plan.get("shots") or []})
    store = JobStore(project / ".satrcut" / "jobs.sqlite")
    gate_log = folder / "gate_log.jsonl"
    halted: str | None = None
    failed = False
    try:
        for shot in plan.get("shots") or []:
            if shot.get("kind") == "card":
                continue
            shot_id = str(shot["id"])
            shot_dir = folder / shot_id
            manifest_path = shot_dir / "manifest.json"
            if manifest_path.exists():
                existing = read_json(manifest_path)
                status = existing.get("status")
                if status in DONE or (status == "FAIL_ALL" and not retry_failed):
                    _say(f"skip {shot_id} ({status})")
                    continue
                if retry_failed and status == "FAIL_ALL":
                    manifest_path.unlink()
            shot_dir.mkdir(parents=True, exist_ok=True)
            partial_path = shot_dir / "partial.json"
            partial: dict[str, Any] = read_json(partial_path) if partial_path.exists() else {
                "shot_id": shot_id,
                "attempts": [],
                "current": None,
                "crops": 0,
                "anchor": shot.get("anchor"),
            }
            start_png, end_png = _prepare(project, plan, shot, shot_dir)
            chosen = None
            for index, step in enumerate(attempt_ladder(shot, plan, max_tries)):
                number = index + 1
                already = next((row for row in partial["attempts"] if row.get("attempt") == number and row.get("verdict")), None)
                if already:
                    _say(f"resume {shot_id} attempt {number} seed {already.get('seed')} {already.get('verdict')}")
                    if already.get("verdict") in DONE:
                        chosen = already
                        break
                    continue
                why = halt_reason(project, need_s, started)
                if why:
                    halted = f"{shot_id} attempt {number}: {why}"
                    _say("HALT " + halted)
                    break
                current = partial.get("current") or {}
                if current.get("state") == "in_flight" and current.get("attempt") == number:
                    if int(current.get("resumes") or 0) >= 1:
                        partial["attempts"].append(
                            {
                                "attempt": number,
                                "seed": current.get("seed"),
                                "preset": current.get("preset"),
                                "verdict": "RENDER_ERROR",
                                "reasons": ["in-flight attempt already retried once after a restart"],
                            }
                        )
                        partial["current"] = None
                        atomic_write_json(partial_path, partial)
                        continue
                    current["resumes"] = int(current.get("resumes") or 0) + 1
                    seed = int(current["seed"])
                    partial["current"] = current
                else:
                    seed = int(step["seed"])
                    partial["current"] = {
                        "attempt": number,
                        "seed": seed,
                        "preset": step["preset_name"],
                        "state": "in_flight",
                        "resumes": 0,
                        "started": now_iso(),
                    }
                atomic_write_json(partial_path, partial)
                store.record_attempt(episode, shot_id, number, seed=seed, status="in_flight")
                attempt_dir = shot_dir / f"a{number}_s{seed}"
                shot_for_render = dict(shot)
                if shot.get("line_audio"):
                    audio = project / str(shot["line_audio"])
                    if audio.is_file():
                        shot_for_render["line_audio"] = str(audio)
                request = RenderRequest(
                    shot=shot_for_render,
                    seed=seed,
                    preset=step["preset"],
                    start_frame=start_png,
                    end_frame=end_png,
                    out_dir=attempt_dir,
                )
                try:
                    result = _render_once(provider, request, backoff)
                except SimulatedCrash:
                    _say(f"HALT {shot_id} attempt {number} seed {seed} (process stopped with partial.json)")
                    atomic_write_json(folder / "status.json", {"state": "halted", "reason": "crash", "shot": shot_id, "updated": now_iso()})
                    return 3
                except (InfraError, OSError, RuntimeError) as exc:
                    record = {
                        "attempt": number,
                        "seed": seed,
                        "preset": step["preset_name"],
                        "verdict": "RENDER_ERROR",
                        "reasons": [str(exc)[:500]],
                    }
                    partial["attempts"].append(record)
                    partial["current"] = None
                    atomic_write_json(partial_path, partial)
                    _append_jsonl(gate_log, {"shot": shot_id, **record})
                    _say(f"render {shot_id} attempt {number} seed {seed} RENDER_ERROR")
                    continue
                qc_out = attempt_dir / "qc.json"
                qc_report = qc_fn(result.mp4, shot.get("qc") or {}, out=qc_out)
                record = {
                    "attempt": number,
                    "seed": seed,
                    "preset": step["preset_name"],
                    "verdict": qc_report.verdict,
                    "reasons": list(qc_report.reasons),
                    "mp4": _rel(project, result.mp4),
                    "sha256": sha256_file(result.mp4),
                    "render_s": result.render_s,
                    "backend": result.backend,
                    "trim": qc_report.clean_window if qc_report.verdict == "TRIM" else None,
                }
                if qc_report.verdict not in DONE:
                    record["remedy_next"] = remedy(qc_report.reasons, shot, start_png, end_png, partial)
                partial["attempts"].append(record)
                partial["current"] = None
                atomic_write_json(partial_path, partial)
                _append_jsonl(gate_log, {"shot": shot_id, **record})
                store.record_attempt(
                    episode, shot_id, number, seed=seed, status="done", verdict=qc_report.verdict,
                    mp4=record["mp4"], sha256=record["sha256"], reasons=qc_report.reasons,
                )
                _say(f"render {shot_id} attempt {number} seed {seed} {qc_report.verdict}")
                if qc_report.verdict in DONE:
                    chosen = record
                    break
            if halted and chosen is None:
                atomic_write_json(folder / "status.json", {"state": "halted", "reason": halted, "updated": now_iso()})
                return 3
            status = chosen["verdict"] if chosen else "FAIL_ALL"
            if status == "FAIL_ALL":
                failed = True
            manifest = {
                "shot_id": shot_id,
                "status": status,
                "backend": provider.name,
                "finished": now_iso(),
                "attempts": partial.get("attempts") or [],
                "chosen": chosen,
                "crops": partial.get("crops") or 0,
                "anchor": partial.get("anchor") or shot.get("anchor"),
            }
            atomic_write_json(manifest_path, manifest)
            if partial_path.exists():
                partial_path.unlink()
            if chosen:
                store.record_shot(
                    episode, shot_id, status=status, seed=chosen.get("seed"), verdict=chosen.get("verdict"),
                    mp4=chosen.get("mp4"), sha256=chosen.get("sha256"),
                )
            else:
                store.record_shot(episode, shot_id, status=status, seed=None, verdict=None, mp4=None, sha256=None)
            if halted:
                atomic_write_json(folder / "status.json", {"state": "halted", "reason": halted, "updated": now_iso()})
                return 3
        atomic_write_json(folder / "status.json", {"state": "done" if not failed else "finished-with-failures", "updated": now_iso()})
    finally:
        store.close()
    return 1 if failed else 0


def collect_status(project: Path, episode: int) -> list[dict[str, Any]]:
    folder = job_dir_for(project, episode)
    rows: list[dict[str, Any]] = []
    if not folder.exists():
        return rows
    names = {path.parent.name for path in folder.glob("*/manifest.json")} | {path.parent.name for path in folder.glob("*/partial.json")}
    for name in sorted(names):
        manifest_path = folder / name / "manifest.json"
        partial_path = folder / name / "partial.json"
        if manifest_path.exists():
            data = read_json(manifest_path)
            chosen = data.get("chosen") or {}
            rows.append(
                {
                    "shot": data.get("shot_id") or name,
                    "status": data.get("status"),
                    "attempt": chosen.get("attempt"),
                    "seed": chosen.get("seed"),
                    "verdict": chosen.get("verdict"),
                    "mp4": chosen.get("mp4") or "",
                }
            )
        elif partial_path.exists():
            data = read_json(partial_path)
            current = data.get("current") or {}
            rows.append(
                {
                    "shot": name,
                    "status": "in_flight" if current else "partial",
                    "attempt": current.get("attempt"),
                    "seed": current.get("seed"),
                    "verdict": "",
                    "mp4": "",
                }
            )
    return rows


def format_status(rows: list[dict[str, Any]]) -> str:
    header = f"{'shot':12} {'status':12} {'try':>4} {'seed':>8} {'verdict':12} file"
    lines = [header]
    for row in rows:
        lines.append(
            f"{str(row['shot']):12} {str(row['status'] or ''):12} {str(row['attempt'] or ''):>4} "
            f"{str(row['seed'] or ''):>8} {str(row['verdict'] or ''):12} {row['mp4']}"
        )
    if not rows:
        lines.append("(no renders yet)")
    return "\n".join(lines)
