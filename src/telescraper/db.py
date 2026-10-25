"""SQLite database management and automatic .env migration for TeleScraper."""

from __future__ import annotations

import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

DB_PATH = Path("telescraper.db")
ENV_PATH = Path(".env")
MEDIA_DIR = Path("media")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def secure_permissions(path: Path) -> None:
    if path.exists() and os.name == "posix":
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


class Database:
    """Thread-safe SQLite database wrapper for settings, group mappings, and messages."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._init_db()
        self.import_from_env()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS group_mappings (
                    chat_id INTEGER PRIMARY KEY,
                    chat_name TEXT NOT NULL,
                    chat_type TEXT NOT NULL DEFAULT 'Group',
                    username TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    discord_enabled INTEGER NOT NULL DEFAULT 1,
                    webhook_url TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    chat_name TEXT NOT NULL DEFAULT '',
                    tg_message_id INTEGER NOT NULL,
                    sender_name TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    reply_to_msg_id INTEGER,
                    reply_author TEXT,
                    reply_snippet TEXT,
                    media_filename TEXT,
                    media_path TEXT,
                    media_mime TEXT,
                    media_size INTEGER NOT NULL DEFAULT 0,
                    discord_status TEXT NOT NULL DEFAULT 'pending',
                    discord_error TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(chat_id, tg_message_id)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_chat_created
                ON messages(chat_id, created_at DESC);
                """
            )
        secure_permissions(self.db_path)

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )

    def get_all_settings(self) -> dict[str, Any]:
        defaults = {
            "tg_api_id": "",
            "tg_api_hash": "",
            "default_webhook_url": "",
            "discord_global_enabled": "1",
            "max_media_mb": "10",
            "theme": "mocha-peach",
        }
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            for r in rows:
                defaults[r["key"]] = r["value"]

        return {
            "tg_api_id": defaults["tg_api_id"],
            "tg_api_hash_configured": bool(defaults["tg_api_hash"]),
            "default_webhook_url": defaults["default_webhook_url"],
            "discord_global_enabled": defaults["discord_global_enabled"] == "1",
            "max_media_mb": float(defaults["max_media_mb"] or 10.0),
            "theme": defaults["theme"],
        }

    def get_raw_credentials(self) -> tuple[str, str]:
        return self.get_setting("tg_api_id"), self.get_setting("tg_api_hash")

    def import_from_env(self) -> bool:
        """Import credentials and initial group mapping from .env if not already stored in SQLite."""
        env_vals: dict[str, str] = {}
        if ENV_PATH.exists():
            env_vals = {k: v for k, v in dotenv_values(ENV_PATH).items() if v is not None}

        for k in ("TG_API_ID", "TG_API_HASH", "DISCORD_WEBHOOK_URL", "SOURCE_CHAT", "SOURCE_CHAT_NAME", "MAX_MEDIA_MB"):
            if os.getenv(k):
                env_vals[k] = os.getenv(k, "")

        if not env_vals:
            return False

        imported_any = False
        if env_vals.get("TG_API_ID") and not self.get_setting("tg_api_id"):
            self.set_setting("tg_api_id", env_vals["TG_API_ID"].strip())
            imported_any = True

        if env_vals.get("TG_API_HASH") and not self.get_setting("tg_api_hash"):
            self.set_setting("tg_api_hash", env_vals["TG_API_HASH"].strip())
            imported_any = True

        if env_vals.get("DISCORD_WEBHOOK_URL") and not self.get_setting("default_webhook_url"):
            self.set_setting("default_webhook_url", env_vals["DISCORD_WEBHOOK_URL"].strip())
            imported_any = True

        if env_vals.get("MAX_MEDIA_MB") and not self.get_setting("max_media_mb"):
            self.set_setting("max_media_mb", env_vals["MAX_MEDIA_MB"].strip())
            imported_any = True

        if not self.get_setting("discord_global_enabled"):
            self.set_setting("discord_global_enabled", "1")

        source_chat = env_vals.get("SOURCE_CHAT", "").strip()
        if source_chat:
            try:
                chat_id = int(source_chat)
                chat_name = env_vals.get("SOURCE_CHAT_NAME", "").strip() or f"Group {chat_id}"
                with self._connect() as conn:
                    existing = conn.execute(
                        "SELECT chat_id FROM group_mappings WHERE chat_id = ?",
                        (chat_id,),
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            """
                            INSERT INTO group_mappings
                            (chat_id, chat_name, chat_type, username, enabled, discord_enabled, webhook_url, updated_at)
                            VALUES (?, ?, 'Supergroup', '', 1, 1, '', ?)
                            """,
                            (chat_id, chat_name, _utc_now()),
                        )
                        imported_any = True
            except ValueError:
                pass

        return imported_any

    def upsert_discovered_groups(self, dialogs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sync Telegram dialogs into group_mappings while preserving enabled and webhook settings."""
        now = _utc_now()
        with self._connect() as conn:
            for d in dialogs:
                conn.execute(
                    """
                    INSERT INTO group_mappings
                        (chat_id, chat_name, chat_type, username, enabled, discord_enabled, webhook_url, updated_at)
                    VALUES (?, ?, ?, ?, 0, 1, '', ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        chat_name = excluded.chat_name,
                        chat_type = excluded.chat_type,
                        username = excluded.username,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(d["id"]),
                        str(d["name"]),
                        str(d.get("type", "Group")),
                        str(d.get("username", "")),
                        now,
                    ),
                )
        return self.list_groups()

    def list_groups(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    g.*,
                    (SELECT COUNT(*) FROM messages m WHERE m.chat_id = g.chat_id) AS message_count,
                    (SELECT MAX(m.created_at) FROM messages m WHERE m.chat_id = g.chat_id) AS last_message_at,
                    (SELECT m.content FROM messages m WHERE m.chat_id = g.chat_id ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS last_message_text,
                    (SELECT m.sender_name FROM messages m WHERE m.chat_id = g.chat_id ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS last_message_sender,
                    (SELECT m.media_filename FROM messages m WHERE m.chat_id = g.chat_id ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS last_message_media
                FROM group_mappings g
                ORDER BY g.enabled DESC, COALESCE((SELECT MAX(m.created_at) FROM messages m WHERE m.chat_id = g.chat_id), g.updated_at) DESC, g.chat_name COLLATE NOCASE ASC
                """
            ).fetchall()
            return [
                {
                    "chat_id": r["chat_id"],
                    "chat_name": r["chat_name"],
                    "chat_type": r["chat_type"],
                    "username": r["username"],
                    "enabled": bool(r["enabled"]),
                    "discord_enabled": bool(r["discord_enabled"]),
                    "webhook_url": r["webhook_url"],
                    "message_count": r["message_count"],
                    "last_message_at": r["last_message_at"],
                    "last_message_text": r["last_message_text"] or "",
                    "last_message_sender": r["last_message_sender"] or "",
                    "last_message_media": r["last_message_media"] or "",
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]

    def get_group(self, chat_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM group_mappings WHERE chat_id = ?",
                (int(chat_id),),
            ).fetchone()
            if not r:
                return None
            return {
                "chat_id": r["chat_id"],
                "chat_name": r["chat_name"],
                "chat_type": r["chat_type"],
                "username": r["username"],
                "enabled": bool(r["enabled"]),
                "discord_enabled": bool(r["discord_enabled"]),
                "webhook_url": r["webhook_url"],
                "updated_at": r["updated_at"],
            }

    def update_group_mapping(
        self,
        chat_id: int,
        enabled: bool | None = None,
        discord_enabled: bool | None = None,
        webhook_url: str | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_group(chat_id)
        if not current:
            return None

        new_enabled = int(enabled) if enabled is not None else int(current["enabled"])
        new_discord = int(discord_enabled) if discord_enabled is not None else int(current["discord_enabled"])
        new_webhook = webhook_url.strip() if webhook_url is not None else current["webhook_url"]

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE group_mappings
                SET enabled = ?, discord_enabled = ?, webhook_url = ?, updated_at = ?
                WHERE chat_id = ?
                """,
                (new_enabled, new_discord, new_webhook, _utc_now(), int(chat_id)),
            )
        return self.get_group(chat_id)

    def get_enabled_chat_ids(self) -> set[int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT chat_id FROM group_mappings WHERE enabled = 1").fetchall()
            return {int(r["chat_id"]) for r in rows}

    def save_message(
        self,
        *,
        chat_id: int,
        chat_name: str,
        tg_message_id: int,
        sender_name: str,
        content: str,
        reply_to_msg_id: int | None = None,
        reply_author: str | None = None,
        reply_snippet: str | None = None,
        media_filename: str | None = None,
        media_path: str | None = None,
        media_mime: str | None = None,
        media_size: int = 0,
        discord_status: str = "pending",
        discord_error: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        ts = created_at or _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (
                    chat_id, chat_name, tg_message_id, sender_name, content,
                    reply_to_msg_id, reply_author, reply_snippet,
                    media_filename, media_path, media_mime, media_size,
                    discord_status, discord_error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, tg_message_id) DO UPDATE SET
                    content = excluded.content,
                    media_filename = COALESCE(excluded.media_filename, messages.media_filename),
                    media_path = COALESCE(excluded.media_path, messages.media_path),
                    media_mime = COALESCE(excluded.media_mime, messages.media_mime),
                    media_size = CASE WHEN excluded.media_size > 0 THEN excluded.media_size ELSE messages.media_size END,
                    discord_status = excluded.discord_status,
                    discord_error = excluded.discord_error
                """,
                (
                    int(chat_id),
                    chat_name,
                    int(tg_message_id),
                    sender_name,
                    content,
                    reply_to_msg_id,
                    reply_author,
                    reply_snippet,
                    media_filename,
                    media_path,
                    media_mime,
                    int(media_size),
                    discord_status,
                    discord_error,
                    ts,
                ),
            )
            row = conn.execute(
                "SELECT * FROM messages WHERE chat_id = ? AND tg_message_id = ?",
                (int(chat_id), int(tg_message_id)),
            ).fetchone()
            return self._row_to_message(row)

    def update_message_discord_status(
        self,
        message_db_id: int,
        status: str,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE messages SET discord_status = ?, discord_error = ? WHERE id = ?",
                (status, error, int(message_db_id)),
            )
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (int(message_db_id),)).fetchone()
            return self._row_to_message(row) if row else None

    def get_message_by_id(self, message_db_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (int(message_db_id),)).fetchone()
            return self._row_to_message(row) if row else None

    def list_messages(
        self,
        chat_id: int | None = None,
        search: str | None = None,
        media_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(int(chat_id))

        if search and search.strip():
            q = f"%{search.strip()}%"
            clauses.append("(content LIKE ? OR sender_name LIKE ? OR chat_name LIKE ? OR media_filename LIKE ?)")
            params.extend([q, q, q, q])

        if media_only:
            clauses.append("media_path IS NOT NULL AND media_path != ''")

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM messages
                {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            # Return chronological order (oldest -> newest) for natural chat reading
            return [self._row_to_message(r) for r in reversed(rows)]

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
        media_path = row["media_path"]
        media_url = f"/media/{media_path}" if media_path else None
        return {
            "id": row["id"],
            "chat_id": row["chat_id"],
            "chat_name": row["chat_name"],
            "tg_message_id": row["tg_message_id"],
            "sender_name": row["sender_name"],
            "content": row["content"],
            "reply_to_msg_id": row["reply_to_msg_id"],
            "reply_author": row["reply_author"],
            "reply_snippet": row["reply_snippet"],
            "media_filename": row["media_filename"],
            "media_path": media_path,
            "media_url": media_url,
            "media_mime": row["media_mime"],
            "media_size": row["media_size"],
            "discord_status": row["discord_status"],
            "discord_error": row["discord_error"],
            "created_at": row["created_at"],
        }
