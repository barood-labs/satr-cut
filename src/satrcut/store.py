"""SQLite index of shots and attempts.

The JSON files (manifest.json, partial.json, gate_log.jsonl) are the checkpoint.
This database is rebuilt from those files, so a crash between the two cannot
lose a finished take. Writes use a transaction and WAL.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from satrcut.util import now_iso, read_json


SCHEMA = """
CREATE TABLE IF NOT EXISTS shots (
  episode INTEGER NOT NULL,
  shot_id TEXT NOT NULL,
  status TEXT,
  seed INTEGER,
  verdict TEXT,
  mp4 TEXT,
  sha256 TEXT,
  updated TEXT,
  PRIMARY KEY (episode, shot_id)
);
CREATE TABLE IF NOT EXISTS attempts (
  episode INTEGER NOT NULL,
  shot_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  seed INTEGER,
  status TEXT,
  verdict TEXT,
  mp4 TEXT,
  sha256 TEXT,
  reasons TEXT,
  updated TEXT,
  PRIMARY KEY (episode, shot_id, attempt)
);
"""


class JobStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def record_attempt(
        self,
        episode: int,
        shot_id: str,
        attempt: int,
        *,
        seed: int,
        status: str,
        verdict: str | None = None,
        mp4: str | None = None,
        sha256: str | None = None,
        reasons: list[str] | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO attempts (episode, shot_id, attempt, seed, status, verdict, mp4, sha256, reasons, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode, shot_id, attempt) DO UPDATE SET
              seed=excluded.seed, status=excluded.status, verdict=excluded.verdict,
              mp4=excluded.mp4, sha256=excluded.sha256, reasons=excluded.reasons, updated=excluded.updated
            """,
            (episode, shot_id, attempt, seed, status, verdict, mp4, sha256, " | ".join(reasons or []), now_iso()),
        )
        self.db.commit()

    def record_shot(self, episode: int, shot_id: str, *, status: str, seed: int | None, verdict: str | None, mp4: str | None, sha256: str | None) -> None:
        self.db.execute(
            """
            INSERT INTO shots (episode, shot_id, status, seed, verdict, mp4, sha256, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode, shot_id) DO UPDATE SET
              status=excluded.status, seed=excluded.seed, verdict=excluded.verdict,
              mp4=excluded.mp4, sha256=excluded.sha256, updated=excluded.updated
            """,
            (episode, shot_id, status, seed, verdict, mp4, sha256, now_iso()),
        )
        self.db.commit()

    def refresh_from_job(self, episode: int, job_dir: Path) -> None:
        if not job_dir.exists():
            return
        for manifest_path in sorted(job_dir.glob("*/manifest.json")):
            data = read_json(manifest_path)
            chosen = data.get("chosen") or {}
            self.record_shot(
                episode,
                str(data.get("shot_id") or manifest_path.parent.name),
                status=str(data.get("status") or ""),
                seed=chosen.get("seed"),
                verdict=chosen.get("verdict"),
                mp4=chosen.get("mp4"),
                sha256=chosen.get("sha256"),
            )
            for attempt in data.get("attempts") or []:
                if attempt.get("attempt") is None:
                    continue
                self.record_attempt(
                    episode,
                    str(data.get("shot_id") or manifest_path.parent.name),
                    int(attempt["attempt"]),
                    seed=int(attempt.get("seed") or 0),
                    status="done",
                    verdict=attempt.get("verdict"),
                    mp4=attempt.get("mp4"),
                    sha256=attempt.get("sha256"),
                    reasons=list(attempt.get("reasons") or []),
                )

    def shot_rows(self, episode: int) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM shots WHERE episode=? ORDER BY shot_id", (episode,)).fetchall()
        return [dict(row) for row in rows]
