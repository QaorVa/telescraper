"""Telethon worker and Discord Webhook relay with local media archiving."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Callable

import aiohttp
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import DocumentAttributeFilename, Message

from .db import MEDIA_DIR, Database, secure_permissions

SESSION_NAME = "userbot_session"


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "attachment.bin"


def extract_sender_name(sender: Any, msg: Message) -> str:
    if sender is not None:
        first = getattr(sender, "first_name", None) or ""
        last = getattr(sender, "last_name", None) or ""
        full_name = f"{first} {last}".strip()
        if full_name:
            return full_name[:80]
        title = getattr(sender, "title", None)
        if title:
            return str(title)[:80]
        username = getattr(sender, "username", None)
        if username:
            return f"@{username}"[:80]

    if getattr(msg, "post_author", None):
        return str(msg.post_author)[:80]
    return "Telegram User"


def infer_filename(msg: Message) -> str:
    if getattr(msg, "file", None) and getattr(msg.file, "name", None):
        return sanitize_filename(str(msg.file.name))

    if getattr(msg, "document", None) and getattr(msg.document, "attributes", None):
        for attr in msg.document.attributes:
            if isinstance(attr, DocumentAttributeFilename) and attr.file_name:
                return sanitize_filename(attr.file_name)

    ext = ""
    if getattr(msg, "file", None):
        ext = getattr(msg.file, "ext", "") or ""
        if not ext and getattr(msg.file, "mime_type", None):
            ext = mimetypes.guess_extension(msg.file.mime_type) or ""

    if not ext:
        ext = ".jpg" if getattr(msg, "photo", None) else ".bin"

    return f"tg_{msg.id}{ext}"


def split_discord_text(text: str, limit: int = 2000) -> list[str]:
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def post_to_discord_webhook(
    session: aiohttp.ClientSession,
    webhook_url: str,
    username: str,
    content: str,
    media_bytes: bytes | None = None,
    filename: str | None = None,
    mime_type: str | None = None,
) -> None:
    chunks = split_discord_text(content)

    for idx, chunk in enumerate(chunks):
        attach_here = idx == 0 and media_bytes is not None and filename is not None
        payload_dict: dict[str, Any] = {"username": username}
        if chunk:
            payload_dict["content"] = chunk

        for _attempt in range(3):
            if attach_here and media_bytes is not None and filename is not None:
                form = aiohttp.FormData()
                form.add_field(
                    "payload_json",
                    json.dumps(payload_dict),
                    content_type="application/json",
                )
                form.add_field(
                    "files[0]",
                    media_bytes,
                    filename=filename,
                    content_type=mime_type or "application/octet-stream",
                )
                resp = await session.post(webhook_url, data=form)
            else:
                resp = await session.post(webhook_url, json=payload_dict)

            async with resp:
                if resp.status in (200, 204):
                    break
                if resp.status == 429:
                    retry_data = await resp.json(content_type=None)
                    retry_after = float(retry_data.get("retry_after", 1.5))
                    await asyncio.sleep(retry_after)
                    continue
                err_body = await resp.text()
                raise RuntimeError(f"Discord HTTP {resp.status}: {err_body[:240]}")


class TelegramWorker:
    """Manages the Telethon client, web authentication flow, group sync, and live message capture."""

    def __init__(self, db: Database, broadcast_cb: Callable[[dict[str, Any]], None]) -> None:
        self.db = db
        self.broadcast_cb = broadcast_cb
        self.client: TelegramClient | None = None
        self.http_session: aiohttp.ClientSession | None = None
        self.auth_state: str = "unconfigured"
        self.account_info: dict[str, Any] | None = None
        self.pending_phone: str | None = None
        self.last_error: str | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self.http_session = aiohttp.ClientSession()
        await self.connect_if_configured()

    async def stop(self) -> None:
        if self.client:
            await self.client.disconnect()
        if self.http_session:
            await self.http_session.close()

    async def connect_if_configured(self) -> dict[str, Any]:
        async with self._lock:
            api_id_str, api_hash = self.db.get_raw_credentials()
            if not api_id_str.isdigit() or not api_hash:
                self.auth_state = "unconfigured"
                self.account_info = None
                return self.get_status()

            if self.client is None:
                self.client = TelegramClient(SESSION_NAME, int(api_id_str), api_hash)
                self.client.flood_sleep_threshold = 60
                self.client.add_event_handler(self._on_new_message, events.NewMessage())

            try:
                if not self.client.is_connected():
                    await self.client.connect()
                    secure_permissions(Path(f"{SESSION_NAME}.session"))

                if await self.client.is_user_authorized():
                    me = await self.client.get_me()
                    self.auth_state = "authorized"
                    self.account_info = {
                        "id": me.id,
                        "first_name": getattr(me, "first_name", "") or "",
                        "last_name": getattr(me, "last_name", "") or "",
                        "username": getattr(me, "username", "") or "",
                        "phone": getattr(me, "phone", "") or "",
                    }
                    self.last_error = None
                    # Sync groups automatically if none are stored yet
                    if not self.db.list_groups():
                        await self._sync_dialogs_unlocked()
                else:
                    if self.auth_state not in ("awaiting_code", "awaiting_password"):
                        self.auth_state = "unauthorized"
                    self.account_info = None
            except Exception as exc:
                self.last_error = f"Could not connect to Telegram: {exc}"
                if self.auth_state not in ("awaiting_code", "awaiting_password"):
                    self.auth_state = "unauthorized"
                self.account_info = None

            return self.get_status()

    def get_status(self) -> dict[str, Any]:
        enabled_ids = self.db.get_enabled_chat_ids()
        return {
            "auth_state": self.auth_state,
            "account": self.account_info,
            "pending_phone": self.pending_phone,
            "enabled_group_count": len(enabled_ids),
            "last_error": self.last_error,
        }

    async def send_login_code(self, phone: str) -> dict[str, Any]:
        await self.connect_if_configured()
        if not self.client:
            raise RuntimeError("Configure Telegram API ID and API Hash first.")
        self.pending_phone = phone.strip()
        await self.client.send_code_request(self.pending_phone)
        self.auth_state = "awaiting_code"
        self.last_error = None
        return self.get_status()

    async def verify_login_code(self, code: str, password: str | None = None) -> dict[str, Any]:
        if not self.client or not self.pending_phone:
            raise RuntimeError("No pending phone login request.")
        try:
            await self.client.sign_in(phone=self.pending_phone, code=code.strip())
        except SessionPasswordNeededError:
            if not password:
                self.auth_state = "awaiting_password"
                return self.get_status()
            await self.client.sign_in(password=password)

        self.pending_phone = None
        return await self.connect_if_configured()

    async def verify_2fa_password(self, password: str) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("Telegram client not initialized.")
        await self.client.sign_in(password=password)
        self.pending_phone = None
        return await self.connect_if_configured()

    async def sync_groups(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await self._sync_dialogs_unlocked()

    async def _sync_dialogs_unlocked(self) -> list[dict[str, Any]]:
        if not self.client or self.auth_state != "authorized":
            return self.db.list_groups()

        dialogs: list[dict[str, Any]] = []
        async for dialog in self.client.iter_dialogs():
            if not (dialog.is_group or dialog.is_channel):
                continue
            name = dialog.name or "Untitled"
            username = getattr(dialog.entity, "username", None)
            if dialog.is_group:
                chat_type = "Supergroup" if getattr(dialog.entity, "megagroup", False) else "Group"
            else:
                chat_type = "Channel"
            dialogs.append(
                {
                    "id": dialog.id,
                    "name": name,
                    "type": chat_type,
                    "username": f"@{username}" if username else "",
                }
            )
        return self.db.upsert_discovered_groups(dialogs)

    async def backfill_recent_messages(self, chat_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent messages from a specific Telegram group into SQLite and local media folder."""
        if not self.client or self.auth_state != "authorized":
            raise RuntimeError("Telegram account is not connected.")

        group = self.db.get_group(chat_id)
        chat_name = group["chat_name"] if group else f"Group {chat_id}"

        captured: list[dict[str, Any]] = []
        messages = await self.client.get_messages(int(chat_id), limit=limit)
        for msg in reversed(messages):
            if not isinstance(msg, Message):
                continue
            saved = await self._process_and_store_message(
                msg=msg,
                chat_id=int(chat_id),
                chat_name=chat_name,
                forward_to_discord=False,
            )
            if saved:
                captured.append(saved)
        return captured

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        chat_id = event.chat_id
        if chat_id is None:
            return

        enabled_ids = self.db.get_enabled_chat_ids()
        if int(chat_id) not in enabled_ids:
            return

        group = self.db.get_group(int(chat_id))
        chat_name = group["chat_name"] if group else f"Group {chat_id}"

        saved = await self._process_and_store_message(
            msg=event.message,
            chat_id=int(chat_id),
            chat_name=chat_name,
            forward_to_discord=True,
        )
        if saved:
            self.broadcast_cb({"type": "new_message", "message": saved})

    async def _process_and_store_message(
        self,
        *,
        msg: Message,
        chat_id: int,
        chat_name: str,
        forward_to_discord: bool,
    ) -> dict[str, Any] | None:
        if not self.client:
            return None

        sender = await msg.get_sender()
        sender_name = extract_sender_name(sender, msg)
        text_content = (msg.message or "").strip()

        reply_to_msg_id: int | None = None
        reply_author: str | None = None
        reply_snippet: str | None = None

        if msg.is_reply and msg.reply_to_msg_id:
            reply_to_msg_id = int(msg.reply_to_msg_id)
            try:
                reply_msg = await msg.get_reply_message()
                if reply_msg:
                    r_sender = await reply_msg.get_sender()
                    reply_author = extract_sender_name(r_sender, reply_msg)
                    reply_snippet = (reply_msg.message or "[Attachment]").replace("\n", " ")[:90]
            except Exception:
                pass

        media_filename: str | None = None
        media_rel_path: str | None = None
        media_mime: str | None = None
        media_size: int = 0
        media_bytes: bytes | None = None

        settings = self.db.get_all_settings()
        max_media_bytes = int(float(settings.get("max_media_mb", 10.0)) * 1024 * 1024)

        if msg.media and not getattr(msg, "web_preview", None):
            media_filename = infer_filename(msg)
            media_mime = getattr(getattr(msg, "file", None), "mime_type", None)
            if not media_mime:
                guessed, _ = mimetypes.guess_type(media_filename)
                media_mime = guessed or ("image/jpeg" if getattr(msg, "photo", None) else "application/octet-stream")

            chat_folder = MEDIA_DIR / str(abs(int(chat_id)))
            chat_folder.mkdir(parents=True, exist_ok=True)
            disk_name = f"{msg.id}_{media_filename}"
            full_disk_path = chat_folder / disk_name
            rel_path = f"{abs(int(chat_id))}/{disk_name}"

            try:
                if not full_disk_path.exists():
                    await self.client.download_media(msg.media, file=str(full_disk_path))
                if full_disk_path.exists():
                    media_bytes = full_disk_path.read_bytes()
                    media_size = len(media_bytes)
                    media_rel_path = rel_path
            except Exception as exc:
                self.last_error = f"Media download failed for msg #{msg.id}: {exc}"

        if not text_content and not media_rel_path:
            return None

        created_iso = msg.date.isoformat() if getattr(msg, "date", None) else None

        # Determine initial Discord status
        group = self.db.get_group(chat_id)
        global_discord_on = bool(settings.get("discord_global_enabled", True))
        group_discord_on = bool(group["discord_enabled"]) if group else True
        webhook_url = (
            (group["webhook_url"].strip() if group and group["webhook_url"].strip() else "")
            or str(settings.get("default_webhook_url", "")).strip()
        )

        if not forward_to_discord:
            initial_status = "archived"
        elif not global_discord_on or not group_discord_on:
            initial_status = "paused"
        elif not webhook_url:
            initial_status = "no_webhook"
        else:
            initial_status = "pending"

        saved_msg = self.db.save_message(
            chat_id=chat_id,
            chat_name=chat_name,
            tg_message_id=msg.id,
            sender_name=sender_name,
            content=text_content,
            reply_to_msg_id=reply_to_msg_id,
            reply_author=reply_author,
            reply_snippet=reply_snippet,
            media_filename=media_filename,
            media_path=media_rel_path,
            media_mime=media_mime,
            media_size=media_size,
            discord_status=initial_status,
            created_at=created_iso,
        )

        if initial_status == "pending" and webhook_url and self.http_session:
            await self.deliver_saved_message_to_discord(
                saved_msg,
                webhook_url=webhook_url,
                media_bytes=media_bytes,
                max_media_bytes=max_media_bytes,
            )
            updated = self.db.get_message_by_id(saved_msg["id"])
            if updated:
                saved_msg = updated

        return saved_msg

    async def deliver_saved_message_to_discord(
        self,
        saved_msg: dict[str, Any],
        *,
        webhook_url: str | None = None,
        media_bytes: bytes | None = None,
        max_media_bytes: int | None = None,
    ) -> dict[str, Any]:
        if not self.http_session:
            raise RuntimeError("HTTP session is not running.")

        settings = self.db.get_all_settings()
        if max_media_bytes is None:
            max_media_bytes = int(float(settings.get("max_media_mb", 10.0)) * 1024 * 1024)

        if not webhook_url:
            group = self.db.get_group(int(saved_msg["chat_id"]))
            webhook_url = (
                (group["webhook_url"].strip() if group and group["webhook_url"].strip() else "")
                or str(settings.get("default_webhook_url", "")).strip()
            )
        if not webhook_url:
            updated = self.db.update_message_discord_status(
                saved_msg["id"],
                "failed",
                "No Discord Webhook URL configured for this group or globally.",
            )
            return updated or saved_msg

        if media_bytes is None and saved_msg.get("media_path"):
            disk_file = MEDIA_DIR / str(saved_msg["media_path"])
            if disk_file.exists():
                media_bytes = disk_file.read_bytes()

        discord_text = saved_msg.get("content") or ""
        if saved_msg.get("reply_author"):
            snippet = saved_msg.get("reply_snippet") or "[Attachment]"
            quote_line = f"> **Reply to {saved_msg['reply_author']}:** {snippet}\n"
            discord_text = f"{quote_line}{discord_text}".strip()

        upload_bytes = media_bytes
        if upload_bytes is not None and len(upload_bytes) > max_media_bytes:
            size_mb = len(upload_bytes) / (1024 * 1024)
            limit_mb = max_media_bytes / (1024 * 1024)
            fname = saved_msg.get("media_filename") or "file"
            discord_text = (
                f"{discord_text}\n*[Attachment `{fname}` ({size_mb:.1f} MB) archived locally; exceeds {limit_mb:.0f} MB Discord limit]*"
            ).strip()
            upload_bytes = None

        try:
            await post_to_discord_webhook(
                session=self.http_session,
                webhook_url=webhook_url,
                username=saved_msg["sender_name"],
                content=discord_text,
                media_bytes=upload_bytes,
                filename=saved_msg.get("media_filename"),
                mime_type=saved_msg.get("media_mime"),
            )
            updated = self.db.update_message_discord_status(saved_msg["id"], "sent", None)
            return updated or saved_msg
        except Exception as exc:
            updated = self.db.update_message_discord_status(saved_msg["id"], "failed", str(exc))
            return updated or saved_msg

    async def send_webhook_test(self, chat_id: int | None = None, custom_url: str | None = None) -> None:
        if not self.http_session:
            raise RuntimeError("HTTP session not initialized.")

        settings = self.db.get_all_settings()
        target_url = (custom_url or "").strip()
        group_label = "Global Default Webhook"

        if chat_id is not None:
            group = self.db.get_group(int(chat_id))
            if group:
                group_label = group["chat_name"]
                if not target_url:
                    target_url = group["webhook_url"].strip()

        if not target_url:
            target_url = str(settings.get("default_webhook_url", "")).strip()

        if not target_url:
            raise ValueError("Configure a Discord Webhook URL before sending a test message.")

        await post_to_discord_webhook(
            session=self.http_session,
            webhook_url=target_url,
            username="TeleScraper Router",
            content=f"Test signal from TeleScraper for **{group_label}**. Webhook connection verified.",
        )
