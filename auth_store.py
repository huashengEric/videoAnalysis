"""
用户认证与用户隔离的博主数据存储
================================
- 用户注册/登录/会话管理（PBKDF2-SHA256，无需额外依赖）
- user_creators 表：每个用户独立的博主列表
- 迁移：首次启动若检测到 creators.json，自动导入到第一个注册用户
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "data.db"
CREATORS_JSON = PROJECT_ROOT / "creators.json"
SESSION_DAYS = 30


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_auth_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL COLLATE NOCASE,
                display_name  TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                is_admin      INTEGER NOT NULL DEFAULT 0,
                created_at    INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_creators (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                name           TEXT NOT NULL,
                url            TEXT NOT NULL,
                platform       TEXT NOT NULL DEFAULT 'douyin',
                max_new_videos INTEGER NOT NULL DEFAULT 5,
                created_at     INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # 轻量迁移：schedules 表补 user_id 列
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(schedules)").fetchall()}
        if "user_id" not in cols and len(cols) > 0:
            conn.execute("ALTER TABLE schedules ADD COLUMN user_id INTEGER;")

        conn.commit()


# ── 密码哈希（Python stdlib PBKDF2-SHA256，无需第三方依赖） ──

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310_000)
    return f"pbkdf2${salt}${dk.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        _, salt, dk_hex = stored_hash.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310_000)
        return secrets.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ── 用户操作 ──

def count_users() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(username: str, password: str, display_name: str = "", is_admin: bool = False) -> Optional[int]:
    try:
        now = int(time.time())
        with _conn() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, display_name, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
                (username.strip(), display_name.strip() or username.strip(), _hash_password(password), 1 if is_admin else 0, now),
            )
            conn.commit()
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # 用户名已存在


def authenticate(username: str, password: str) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
        ).fetchone()
    if not row:
        return None
    if not _verify_password(password, row["password_hash"]):
        return None
    return dict(row)


def get_user_by_id(user_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


# ── 会话操作 ──

def create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    expires_at = int(time.time()) + SESSION_DAYS * 86400
    with _conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at),
        )
        conn.commit()
    return token


def get_session_user(token: str) -> Optional[dict[str, Any]]:
    now = int(time.time())
    with _conn() as conn:
        row = conn.execute(
            """SELECT u.* FROM sessions s
               JOIN users u ON s.user_id = u.id
               WHERE s.token = ? AND s.expires_at > ?""",
            (token, now),
        ).fetchone()
        return dict(row) if row else None


def delete_session(token: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


# ── 用户博主操作（隔离） ──

def list_creators(user_id: int) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM user_creators WHERE user_id = ? ORDER BY created_at ASC, id ASC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_creator(user_id: int, *, name: str, url: str, platform: str, max_new_videos: int) -> dict[str, Any]:
    now = int(time.time())
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO user_creators (user_id, name, url, platform, max_new_videos, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, url, platform, max_new_videos, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM user_creators WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def get_creator(creator_id: int, user_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_creators WHERE id = ? AND user_id = ?", (creator_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def update_creator(creator_id: int, user_id: int, *, name: str, url: str, platform: str, max_new_videos: int) -> bool:
    with _conn() as conn:
        r = conn.execute(
            "UPDATE user_creators SET name=?, url=?, platform=?, max_new_videos=? WHERE id=? AND user_id=?",
            (name, url, platform, max_new_videos, creator_id, user_id),
        )
        conn.commit()
        return r.rowcount > 0


def delete_creator(creator_id: int, user_id: int) -> bool:
    with _conn() as conn:
        r = conn.execute(
            "DELETE FROM user_creators WHERE id=? AND user_id=?", (creator_id, user_id)
        )
        conn.commit()
        return r.rowcount > 0


def migrate_creators_json(user_id: int) -> int:
    """将 creators.json 中的博主导入到指定用户，仅执行一次（文件存在时才运行）。"""
    if not CREATORS_JSON.exists():
        return 0
    try:
        data = json.loads(CREATORS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(data, list) or not data:
        return 0
    existing = {(c["url"], c.get("platform", "douyin")) for c in list_creators(user_id)}
    count = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        platform = str(item.get("platform", "douyin")).strip()
        if not url or (url, platform) in existing:
            continue
        add_creator(
            user_id,
            name=str(item.get("name", url)).strip(),
            url=url,
            platform=platform,
            max_new_videos=int(item.get("max_new_videos", 5)),
        )
        count += 1
    return count
