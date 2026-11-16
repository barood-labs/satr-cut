"""Local review server. Binds to 127.0.0.1 only.

Shot cards, QC reasons, approve / reject / retake, the render queue, and a
cost estimate from local timings and the series rate card. The estimate is not
a charge. Mock time is priced at whatever `cost.gpu_usd_per_hour` says, which
defaults to 0.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from satrcut.project import episode_plan_path, find_project, load_series
from satrcut.runner import collect_status, job_dir_for
from satrcut.util import atomic_write_json, read_json

HTML = (Path(__file__).resolve().parent / "data" / "studio.html").read_text(encoding="utf-8")


def reviews_path(project: Path) -> Path:
    return project / ".satrcut" / "reviews.json"


def load_reviews(project: Path) -> dict[str, Any]:
    path = reviews_path(project)
    if not path.is_file():
        return {}
    return read_json(path)


def cost_report(project: Path, episode: int) -> dict[str, Any]:
    series = load_series(project)
    rate = float((series.raw.get("cost") or {}).get("gpu_usd_per_hour") or 0)
    folder = job_dir_for(project, episode)
    shots = []
    seconds = 0.0
    if folder.is_dir():
        for manifest_path in sorted(folder.glob("*/manifest.json")):
            data = read_json(manifest_path)
            shot_seconds = 0.0
            for attempt in data.get("attempts") or []:
                shot_seconds += float(attempt.get("render_s") or 0)
            seconds += shot_seconds
            shots.append(
                {
                    "shot": data.get("shot_id") or manifest_path.parent.name,
                    "render_s": round(shot_seconds, 3),
                    "usd": round(shot_seconds / 3600 * rate, 6),
                    "status": data.get("status"),
                }
            )
    hours = seconds / 3600
    return {
        "episode": episode,
        "gpu_hours": round(hours, 6),
        "usd": round(hours * rate, 6),
        "gpu_usd_per_hour": rate,
        "billed": False,
        "note": "Estimate from local render times and the rate card. Nothing is charged.",
        "shots": shots,
    }


def episode_payload(project: Path, episode: int) -> dict[str, Any]:
    plan_path = episode_plan_path(project, episode)
    plan = read_json(plan_path) if plan_path.is_file() else {"shots": [], "cuts": [], "episode": episode}
    reviews = load_reviews(project)
    status = {row["shot"]: row for row in collect_status(project, episode)}
    cards = []
    for shot in plan.get("shots") or []:
        shot_id = shot["id"]
        row = status.get(shot_id, {})
        qc_reasons: list[str] = []
        manifest_path = job_dir_for(project, episode) / shot_id / "manifest.json"
        if manifest_path.is_file():
            manifest = read_json(manifest_path)
            chosen = manifest.get("chosen") or {}
            qc_reasons = list(chosen.get("reasons") or [])
            if not qc_reasons and manifest.get("attempts"):
                qc_reasons = list((manifest["attempts"][-1] or {}).get("reasons") or [])
        cards.append(
            {
                "id": shot_id,
                "shot_type": shot.get("shot_type"),
                "line": shot.get("line") or "",
                "speaker": shot.get("speaker") or "",
                "grade": shot.get("grade"),
                "status": row.get("status") or "queued",
                "verdict": row.get("verdict") or "",
                "seed": row.get("seed"),
                "attempt": row.get("attempt"),
                "reasons": qc_reasons,
                "review": reviews.get(f"{episode}:{shot_id}"),
            }
        )
    return {
        "episode": episode,
        "title": plan.get("title") or f"Episode {episode}",
        "emotional_beat": plan.get("emotional_beat") or "",
        "ending": plan.get("ending"),
        "runtime_s": plan.get("runtime_s"),
        "cuts": [
            {"n": cut.get("n"), "at": cut.get("at"), "dur": cut.get("dur"), "source": cut.get("source"), "framing": cut.get("framing")}
            for cut in plan.get("cuts") or []
        ],
        "shots": cards,
        "queue": [card for card in cards if card["status"] in ("queued", "in_flight", "partial")],
        "cost": cost_report(project, episode),
    }


def decide(project: Path, episode: int, shot_id: str, action: str) -> dict[str, Any]:
    if action not in ("approve", "reject", "retake"):
        raise ValueError("action must be approve, reject, or retake")
    folder = job_dir_for(project, episode) / shot_id
    manifest_path = folder / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.is_file() else None
    if action == "approve":
        if manifest and manifest.get("status") not in ("PASS", "TRIM", "REVIEW"):
            raise ValueError(f"{shot_id} is {manifest.get('status')}. Retake it instead of approving a failed take.")
        if manifest:
            manifest["human_accept"] = True
            if manifest.get("status") == "REVIEW":
                manifest["status"] = "REVIEW"
            atomic_write_json(manifest_path, manifest)
    elif action == "reject":
        if manifest:
            manifest["rejected"] = True
            atomic_write_json(manifest_path, manifest)
    elif action == "retake":
        if manifest_path.exists():
            manifest_path.unlink()
        partial = folder / "partial.json"
        if partial.exists():
            partial.unlink()
    reviews = load_reviews(project)
    from satrcut.util import now_iso

    reviews[f"{episode}:{shot_id}"] = {"action": action, "at": now_iso()}
    atomic_write_json(reviews_path(project), reviews)
    return reviews[f"{episode}:{shot_id}"]


def make_handler(project: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return None

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: Any) -> None:
            self._send(code, json.dumps(payload).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/studio"):
                self._send(200, HTML.encode(), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/health":
                self._json(200, {"ok": True, "project": project.name})
                return
            parts = [part for part in parsed.path.split("/") if part]
            query = parse_qs(parsed.query)
            if parts[:2] == ["api", "episode"] and len(parts) == 3:
                self._json(200, episode_payload(project, int(parts[2])))
                return
            if parts[:2] == ["api", "queue"]:
                episode = int((query.get("episode") or ["1"])[0])
                payload = episode_payload(project, episode)
                self._json(200, {"queue": payload["queue"]})
                return
            if parts[:2] == ["api", "cost"]:
                episode = int((query.get("episode") or ["1"])[0])
                self._json(200, cost_report(project, episode))
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            parts = [part for part in urlparse(self.path).path.split("/") if part]
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            if len(parts) == 5 and parts[:2] == ["api", "episode"] and parts[3] == "shot":
                try:
                    result = decide(project, int(parts[2]), parts[4], str(body.get("action") or ""))
                except (ValueError, OSError) as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(200, result)
                return
            self._json(404, {"error": "not found"})

    return Handler


def serve(project: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("studio binds to localhost only")
    httpd = ThreadingHTTPServer((host, port), make_handler(project.resolve()))
    return httpd


def main_serve(project: Path | None = None, port: int = 8765) -> None:
    root = find_project(project)
    httpd = serve(root, port=port)
    bound = httpd.server_address[1]
    print(f"satrcut studio  http://127.0.0.1:{bound}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
