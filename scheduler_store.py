from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "data.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS analyzed_videos (
              aweme_id TEXT PRIMARY KEY,
              title TEXT,
              video_url TEXT,
              creator_url TEXT,
              created_at INTEGER NOT NULL,
              deepseek_filename TEXT
            );

            CREATE TABLE IF NOT EXISTS schedules (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              hour INTEGER NOT NULL,
              minute INTEGER NOT NULL,
              max_new_per_creator INTEGER NOT NULL DEFAULT 5,
              creator_indices_json TEXT NOT NULL, -- JSON array of ints, empty means all
              report_email TEXT,
              generate_summary INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schedule_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              schedule_id INTEGER NOT NULL,
              started_at INTEGER NOT NULL,
              finished_at INTEGER,
              status TEXT NOT NULL, -- running/success/failed
              detail TEXT,
              FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
            );
            """
        )
        # 轻量迁移：为旧表补齐新字段
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(schedules)").fetchall()}
        if "report_email" not in cols:
            conn.execute("ALTER TABLE schedules ADD COLUMN report_email TEXT;")
        if "generate_summary" not in cols:
            conn.execute("ALTER TABLE schedules ADD COLUMN generate_summary INTEGER NOT NULL DEFAULT 0;")


def is_analyzed(aweme_id: str) -> bool:
    if not aweme_id:
        return False
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM analyzed_videos WHERE aweme_id = ? LIMIT 1", (aweme_id,)
        ).fetchone()
        return bool(row)


def mark_analyzed(
    *,
    aweme_id: str,
    title: str | None = None,
    video_url: str | None = None,
    creator_url: str | None = None,
    deepseek_filename: str | None = None,
) -> None:
    if not aweme_id:
        return
    now = int(time.time())
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO analyzed_videos (aweme_id, title, video_url, creator_url, created_at, deepseek_filename)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(aweme_id) DO UPDATE SET
              title=COALESCE(excluded.title, analyzed_videos.title),
              video_url=COALESCE(excluded.video_url, analyzed_videos.video_url),
              creator_url=COALESCE(excluded.creator_url, analyzed_videos.creator_url),
              deepseek_filename=COALESCE(excluded.deepseek_filename, analyzed_videos.deepseek_filename)
            """,
            (aweme_id, title, video_url, creator_url, now, deepseek_filename),
        )


@dataclass
class Schedule:
    id: int
    name: str
    enabled: bool
    hour: int
    minute: int
    max_new_per_creator: int
    creator_indices: list[int]  # empty means all
    report_email: str | None
    generate_summary: bool
    created_at: int
    updated_at: int


def _row_to_schedule(row: sqlite3.Row) -> Schedule:
    return Schedule(
        id=int(row["id"]),
        name=str(row["name"]),
        enabled=bool(row["enabled"]),
        hour=int(row["hour"]),
        minute=int(row["minute"]),
        max_new_per_creator=int(row["max_new_per_creator"]),
        creator_indices=list(json.loads(row["creator_indices_json"] or "[]")),
        report_email=(str(row["report_email"]).strip() if row["report_email"] is not None else None),
        generate_summary=bool(row["generate_summary"]) if "generate_summary" in row.keys() else False,
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def list_schedules() -> list[Schedule]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM schedules ORDER BY id DESC").fetchall()
        return [_row_to_schedule(r) for r in rows]


def get_schedule(schedule_id: int) -> Schedule | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        return _row_to_schedule(row) if row else None


def create_schedule(
    *,
    name: str,
    hour: int,
    minute: int,
    max_new_per_creator: int,
    creator_indices: list[int],
    report_email: str | None = None,
    generate_summary: bool = False,
    enabled: bool = True,
) -> Schedule:
    now = int(time.time())
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO schedules (name, enabled, hour, minute, max_new_per_creator, creator_indices_json, report_email, generate_summary, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                1 if enabled else 0,
                hour,
                minute,
                max_new_per_creator,
                json.dumps(creator_indices, ensure_ascii=False),
                report_email.strip() if report_email else None,
                1 if generate_summary else 0,
                now,
                now,
            ),
        )
        sid = int(cur.lastrowid)
    sch = get_schedule(sid)
    assert sch
    return sch


def update_schedule(
    schedule_id: int,
    *,
    name: str,
    hour: int,
    minute: int,
    max_new_per_creator: int,
    creator_indices: list[int],
    report_email: str | None,
    generate_summary: bool,
    enabled: bool,
) -> Schedule | None:
    now = int(time.time())
    with _connect() as conn:
        conn.execute(
            """
            UPDATE schedules
            SET name=?, enabled=?, hour=?, minute=?, max_new_per_creator=?, creator_indices_json=?, report_email=?, generate_summary=?, updated_at=?
            WHERE id=?
            """,
            (
                name,
                1 if enabled else 0,
                hour,
                minute,
                max_new_per_creator,
                json.dumps(creator_indices, ensure_ascii=False),
                report_email.strip() if report_email else None,
                1 if generate_summary else 0,
                now,
                schedule_id,
            ),
        )
    return get_schedule(schedule_id)


def delete_schedule(schedule_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))


def create_run(schedule_id: int) -> int:
    now = int(time.time())
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO schedule_runs (schedule_id, started_at, status)
            VALUES (?, ?, 'running')
            """,
            (schedule_id, now),
        )
        return int(cur.lastrowid)


def finish_run(run_id: int, *, status: str, detail: str | None = None) -> None:
    now = int(time.time())
    with _connect() as conn:
        conn.execute(
            """
            UPDATE schedule_runs SET finished_at=?, status=?, detail=? WHERE id=?
            """,
            (now, status, detail, run_id),
        )


def list_runs(schedule_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM schedule_runs WHERE schedule_id=? ORDER BY id DESC LIMIT ?
            """,
            (schedule_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]

