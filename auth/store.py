"""Base de metadatos de la app (data/app.db): usuarios, sesiones, proyectos, miembros,
chats compartidos y tokens de Google.

El id de usuario es el mail en minúsculas (lo que verifica Google). Se guarda
también el `sub` de Google para detectar si una cuenta cambió de dueño.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .. import config

ROLES = ("owner", "editor", "viewer")
USER_ROLES = ("admin", "member")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,               -- mail en minúsculas
  google_sub TEXT UNIQUE,
  name TEXT,
  picture TEXT,
  role TEXT NOT NULL DEFAULT 'member',
  status TEXT NOT NULL DEFAULT 'active',  -- active | disabled
  invited_by TEXT,
  created_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY,       -- sha256 del token de la cookie (el token no se guarda)
  user_id TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  user_agent TEXT
);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user ON auth_sessions(user_id);

CREATE TABLE IF NOT EXISTS google_tokens (
  user_id TEXT PRIMARY KEY REFERENCES users(id),
  refresh_token TEXT NOT NULL,       -- cifrado (Fernet)
  scopes TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS project_members (
  project_id TEXT NOT NULL REFERENCES projects(id),
  user_id TEXT NOT NULL,
  role TEXT NOT NULL,
  added_by TEXT NOT NULL,
  added_at TEXT NOT NULL,
  PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_members_user ON project_members(user_id);

CREATE TABLE IF NOT EXISTS chat_shares (
  id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL,
  owner TEXT NOT NULL,
  project_id TEXT NOT NULL REFERENCES projects(id),
  title TEXT NOT NULL,
  items TEXT NOT NULL,               -- copia de solo lectura de los mensajes
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_shares_project ON chat_shares(project_id);
"""


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_email(email: str) -> str:
    return (email or "").strip().lower()


class AppStore:
    def __init__(self, path: Path = config.APP_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _read(self, sql: str, args: tuple | list = ()) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def _write(self, sql: str, args: tuple | list = ()) -> int:
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    return conn.execute(sql, args).rowcount
            finally:
                conn.close()

    # --- usuarios -----------------------------------------------------------------------

    def user(self, user_id: str) -> dict[str, Any] | None:
        rows = self._read("SELECT * FROM users WHERE id = ?", (clean_email(user_id),))
        return rows[0] if rows else None

    def users(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM users ORDER BY id")

    def add_user(self, email: str, *, role: str = "member", invited_by: str | None = None) -> dict[str, Any]:
        email = clean_email(email)
        self._write(
            "INSERT OR IGNORE INTO users (id, role, status, invited_by, created_at) VALUES (?, ?, 'active', ?, ?)",
            (email, role, invited_by, now_iso()),
        )
        return self.user(email)  # type: ignore[return-value]

    def record_login(self, email: str, sub: str, name: str | None, picture: str | None, role: str) -> dict[str, Any]:
        email = clean_email(email)
        at = now_iso()
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO users (id, role, status, created_at) VALUES (?, ?, 'active', ?)",
                        (email, role, at),
                    )
                    conn.execute(
                        "UPDATE users SET google_sub = ?, name = ?, picture = ?, last_login_at = ? WHERE id = ?",
                        (sub, name, picture, at, email),
                    )
            finally:
                conn.close()
        return self.user(email)  # type: ignore[return-value]

    def update_user(self, email: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {k: v for k, v in fields.items() if k in ("role", "status", "name") and v is not None}
        if allowed:
            sets = ", ".join(f"{k} = ?" for k in allowed)
            self._write(f"UPDATE users SET {sets} WHERE id = ?", [*allowed.values(), clean_email(email)])
        return self.user(email)

    # --- sesiones -------------------------------------------------------------------------

    def create_session(self, user_id: str, days: float, user_agent: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        now = dt.datetime.now(dt.UTC)
        expires = (now + dt.timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        self._write(
            "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at, last_seen_at, user_agent)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (_hash(token), clean_email(user_id), at, expires, at, (user_agent or "")[:300]),
        )
        return token

    def session_user(self, token: str) -> dict[str, Any] | None:
        """Usuario de una sesión vigente, o None. Actualiza `last_seen_at` como mucho cada 5 minutos."""
        if not token:
            return None
        rows = self._read(
            "SELECT s.token_hash, s.expires_at, s.last_seen_at, u.* FROM auth_sessions s"
            " JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (_hash(token),),
        )
        if not rows:
            return None
        row = rows[0]
        now = now_iso()
        if row["expires_at"] <= now or row["status"] != "active":
            return None
        if row["last_seen_at"][:15] != now[:15]:  # cambia el minuto de a 10: alcanza para "visto hace poco"
            self._write("UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?", (now, row["token_hash"]))
        return {k: row[k] for k in ("id", "name", "picture", "role", "status")}

    def delete_session(self, token: str) -> None:
        self._write("DELETE FROM auth_sessions WHERE token_hash = ?", (_hash(token),))

    def delete_user_sessions(self, user_id: str) -> int:
        return self._write("DELETE FROM auth_sessions WHERE user_id = ?", (clean_email(user_id),))

    def purge_expired_sessions(self) -> int:
        return self._write("DELETE FROM auth_sessions WHERE expires_at <= ?", (now_iso(),))

    # --- tokens de Google -------------------------------------------------------------------

    def put_google_token(self, user_id: str, encrypted_refresh: str, scopes: str) -> None:
        self._write(
            "INSERT OR REPLACE INTO google_tokens (user_id, refresh_token, scopes, updated_at) VALUES (?, ?, ?, ?)",
            (clean_email(user_id), encrypted_refresh, scopes, now_iso()),
        )

    def google_token(self, user_id: str) -> dict[str, Any] | None:
        rows = self._read("SELECT * FROM google_tokens WHERE user_id = ?", (clean_email(user_id),))
        return rows[0] if rows else None

    def delete_google_token(self, user_id: str) -> None:
        self._write("DELETE FROM google_tokens WHERE user_id = ?", (clean_email(user_id),))

    # --- proyectos -------------------------------------------------------------------------

    def create_project(self, name: str, owner: str) -> dict[str, Any]:
        pid = "p_" + secrets.token_hex(5)
        at = now_iso()
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO projects (id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
                        (pid, name, clean_email(owner), at),
                    )
                    conn.execute(
                        "INSERT INTO project_members (project_id, user_id, role, added_by, added_at) VALUES (?, ?, 'owner', ?, ?)",
                        (pid, clean_email(owner), clean_email(owner), at),
                    )
            finally:
                conn.close()
        return self.project(pid)  # type: ignore[return-value]

    def project(self, pid: str) -> dict[str, Any] | None:
        rows = self._read("SELECT * FROM projects WHERE id = ?", (pid,))
        return rows[0] if rows else None

    def rename_project(self, pid: str, name: str) -> None:
        self._write("UPDATE projects SET name = ? WHERE id = ?", (name, pid))

    def archive_project(self, pid: str, archived: bool = True) -> None:
        self._write("UPDATE projects SET archived = ? WHERE id = ?", (int(archived), pid))

    def user_projects(self, user_id: str) -> list[dict[str, Any]]:
        return self._read(
            "SELECT p.*, m.role FROM projects p JOIN project_members m ON m.project_id = p.id"
            " WHERE m.user_id = ? AND p.archived = 0 ORDER BY lower(p.name)",
            (clean_email(user_id),),
        )

    def role(self, pid: str, user_id: str) -> str | None:
        rows = self._read(
            "SELECT m.role FROM project_members m JOIN projects p ON p.id = m.project_id"
            " WHERE m.project_id = ? AND m.user_id = ? AND p.archived = 0",
            (pid, clean_email(user_id)),
        )
        return rows[0]["role"] if rows else None

    def members(self, pid: str) -> list[dict[str, Any]]:
        return self._read(
            "SELECT m.user_id, m.role, m.added_by, m.added_at, u.name, u.picture, u.last_login_at"
            " FROM project_members m LEFT JOIN users u ON u.id = m.user_id"
            " WHERE m.project_id = ? ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'editor' THEN 1 ELSE 2 END, m.user_id",
            (pid,),
        )

    def set_member(self, pid: str, user_id: str, role: str, added_by: str) -> None:
        self._write(
            "INSERT INTO project_members (project_id, user_id, role, added_by, added_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(project_id, user_id) DO UPDATE SET role = excluded.role",
            (pid, clean_email(user_id), role, clean_email(added_by), now_iso()),
        )

    def remove_member(self, pid: str, user_id: str) -> None:
        self._write("DELETE FROM project_members WHERE project_id = ? AND user_id = ?", (pid, clean_email(user_id)))

    # --- chats compartidos ---------------------------------------------------------------------

    def create_share(self, chat_id: str, owner: str, pid: str, title: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        sid = "s_" + secrets.token_hex(6)
        self._write(
            "INSERT INTO chat_shares (id, chat_id, owner, project_id, title, items, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, chat_id, clean_email(owner), pid, title, json.dumps(items, ensure_ascii=False, default=str), now_iso()),
        )
        return self.share(sid)  # type: ignore[return-value]

    def share(self, sid: str) -> dict[str, Any] | None:
        rows = self._read("SELECT * FROM chat_shares WHERE id = ?", (sid,))
        if not rows:
            return None
        row = rows[0]
        row["items"] = json.loads(row["items"])
        return row

    def project_shares(self, pid: str) -> list[dict[str, Any]]:
        return self._read(
            "SELECT id, chat_id, owner, project_id, title, created_at FROM chat_shares WHERE project_id = ? ORDER BY created_at DESC",
            (pid,),
        )

    def delete_share(self, sid: str) -> None:
        self._write("DELETE FROM chat_shares WHERE id = ?", (sid,))
