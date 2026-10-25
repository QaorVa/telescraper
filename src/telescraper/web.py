"""aiohttp web server providing REST API, SSE live stream, local media serving, and React SPA hosting."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp import web

from .db import MEDIA_DIR, Database
from .telegram_worker import TelegramWorker

WEB_DIST_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


class WebServer:
    """Self-hosted web server for TeleScraper."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.sse_queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self.worker = TelegramWorker(db=self.db, broadcast_cb=self.broadcast_event)
        self.app = web.Application(middlewares=[self._cors_middleware])
        self._setup_routes()
        self.app.on_startup.append(self._on_startup)
        self.app.on_cleanup.append(self._on_cleanup)

    def broadcast_event(self, payload: dict[str, Any]) -> None:
        for q in list(self.sse_queues):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler: Any) -> web.StreamResponse:
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
        else:
            try:
                resp = await handler(request)
            except web.HTTPException as http_exc:
                resp = http_exc
            except Exception as exc:
                resp = web.json_response({"error": str(exc)}, status=500)

        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, PUT, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    async def _on_startup(self, _app: web.Application) -> None:
        await self.worker.start()

    async def _on_cleanup(self, _app: web.Application) -> None:
        await self.worker.stop()

    def _setup_routes(self) -> None:
        self.app.router.add_get("/api/state", self.handle_get_state)
        self.app.router.add_patch("/api/settings", self.handle_update_settings)
        self.app.router.add_post("/api/auth/send-code", self.handle_send_code)
        self.app.router.add_post("/api/auth/verify-code", self.handle_verify_code)
        self.app.router.add_post("/api/auth/verify-password", self.handle_verify_password)
        self.app.router.add_get("/api/groups", self.handle_list_groups)
        self.app.router.add_post("/api/groups/sync", self.handle_sync_groups)
        self.app.router.add_patch("/api/groups/{chat_id}", self.handle_update_group)
        self.app.router.add_post("/api/groups/{chat_id}/backfill", self.handle_backfill_group)
        self.app.router.add_post("/api/webhook/test", self.handle_test_webhook)
        self.app.router.add_get("/api/messages", self.handle_list_messages)
        self.app.router.add_post("/api/messages/{msg_id}/retry-discord", self.handle_retry_discord)
        self.app.router.add_get("/api/stream", self.handle_sse_stream)

        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        self.app.router.add_static("/media/", path=str(MEDIA_DIR), show_index=False)

        if WEB_DIST_DIR.exists():
            assets_dir = WEB_DIST_DIR / "assets"
            if assets_dir.exists():
                self.app.router.add_static("/assets/", path=str(assets_dir), show_index=False)
            self.app.router.add_get("/{tail:.*}", self.handle_spa_index)

    async def handle_get_state(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "settings": self.db.get_all_settings(),
                "telegram": self.worker.get_status(),
                "groups": self.db.list_groups(),
            }
        )

    async def handle_update_settings(self, request: web.Request) -> web.Response:
        body = await request.json()
        reconnect_needed = False

        if "tg_api_id" in body and str(body["tg_api_id"]).strip():
            self.db.set_setting("tg_api_id", str(body["tg_api_id"]).strip())
            reconnect_needed = True

        if "tg_api_hash" in body and str(body["tg_api_hash"]).strip():
            self.db.set_setting("tg_api_hash", str(body["tg_api_hash"]).strip())
            reconnect_needed = True

        if "default_webhook_url" in body:
            self.db.set_setting("default_webhook_url", str(body["default_webhook_url"]).strip())

        if "discord_global_enabled" in body:
            val = "1" if body["discord_global_enabled"] else "0"
            self.db.set_setting("discord_global_enabled", val)

        if "max_media_mb" in body:
            self.db.set_setting("max_media_mb", str(float(body["max_media_mb"])))

        if "theme" in body and body["theme"] in ("mocha-peach", "latte-peach"):
            self.db.set_setting("theme", str(body["theme"]))

        if reconnect_needed:
            await self.worker.connect_if_configured()

        settings = self.db.get_all_settings()
        self.broadcast_cb_state()
        return web.json_response(
            {
                "settings": settings,
                "telegram": self.worker.get_status(),
            }
        )

    def broadcast_cb_state(self) -> None:
        self.broadcast_event(
            {
                "type": "state_updated",
                "settings": self.db.get_all_settings(),
                "telegram": self.worker.get_status(),
            }
        )

    async def handle_send_code(self, request: web.Request) -> web.Response:
        body = await request.json()
        phone = str(body.get("phone", "")).strip()
        if not phone:
            return web.json_response({"error": "Enter your phone number in international format (+...)."}, status=400)
        status = await self.worker.send_login_code(phone)
        return web.json_response({"telegram": status})

    async def handle_verify_code(self, request: web.Request) -> web.Response:
        body = await request.json()
        code = str(body.get("code", "")).strip()
        password = body.get("password")
        if not code:
            return web.json_response({"error": "Enter the 5-digit verification code from Telegram."}, status=400)
        status = await self.worker.verify_login_code(code=code, password=password)
        return web.json_response({"telegram": status, "groups": self.db.list_groups()})

    async def handle_verify_password(self, request: web.Request) -> web.Response:
        body = await request.json()
        password = str(body.get("password", ""))
        if not password:
            return web.json_response({"error": "Enter your 2FA password."}, status=400)
        status = await self.worker.verify_2fa_password(password=password)
        return web.json_response({"telegram": status, "groups": self.db.list_groups()})

    async def handle_list_groups(self, _request: web.Request) -> web.Response:
        return web.json_response({"groups": self.db.list_groups()})

    async def handle_sync_groups(self, _request: web.Request) -> web.Response:
        groups = await self.worker.sync_groups()
        return web.json_response({"groups": groups, "telegram": self.worker.get_status()})

    async def handle_update_group(self, request: web.Request) -> web.Response:
        chat_id = int(request.match_info["chat_id"])
        body = await request.json()

        updated = self.db.update_group_mapping(
            chat_id=chat_id,
            enabled=body.get("enabled"),
            discord_enabled=body.get("discord_enabled"),
            webhook_url=body.get("webhook_url"),
        )
        if not updated:
            return web.json_response({"error": f"Group {chat_id} not found."}, status=404)

        self.broadcast_cb_state()
        return web.json_response(
            {
                "group": updated,
                "groups": self.db.list_groups(),
                "telegram": self.worker.get_status(),
            }
        )

    async def handle_backfill_group(self, request: web.Request) -> web.Response:
        chat_id = int(request.match_info["chat_id"])
        body = await request.json() if request.can_read_body else {}
        limit = int(body.get("limit", 20))
        captured = await self.worker.backfill_recent_messages(chat_id=chat_id, limit=min(max(limit, 1), 50))
        return web.json_response(
            {
                "captured_count": len(captured),
                "messages": self.db.list_messages(chat_id=chat_id, limit=100),
                "groups": self.db.list_groups(),
            }
        )

    async def handle_test_webhook(self, request: web.Request) -> web.Response:
        body = await request.json()
        chat_id = body.get("chat_id")
        custom_url = body.get("webhook_url")
        await self.worker.send_webhook_test(
            chat_id=int(chat_id) if chat_id is not None else None,
            custom_url=custom_url,
        )
        return web.json_response({"ok": True})

    async def handle_list_messages(self, request: web.Request) -> web.Response:
        chat_id_raw = request.query.get("chat_id")
        chat_id = int(chat_id_raw) if chat_id_raw and chat_id_raw not in ("all", "") else None
        search = request.query.get("search")
        media_only = request.query.get("media_only") == "1"
        limit = int(request.query.get("limit", "100"))
        messages = self.db.list_messages(
            chat_id=chat_id,
            search=search,
            media_only=media_only,
            limit=min(max(limit, 1), 300),
        )
        return web.json_response({"messages": messages})

    async def handle_retry_discord(self, request: web.Request) -> web.Response:
        msg_id = int(request.match_info["msg_id"])
        msg = self.db.get_message_by_id(msg_id)
        if not msg:
            return web.json_response({"error": "Message not found."}, status=404)

        updated = await self.worker.deliver_saved_message_to_discord(msg)
        return web.json_response({"message": updated})

    async def handle_sse_stream(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(request)

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self.sse_queues.add(queue)

        try:
            await response.write(b'data: {"type": "connected"}\n\n')
            while True:
                try:
                    event_data = await asyncio.wait_for(queue.get(), timeout=20.0)
                    payload = f"data: {json.dumps(event_data)}\n\n".encode("utf-8")
                    await response.write(payload)
                except asyncio.TimeoutError:
                    await response.write(b": heartbeat\n\n")
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            self.sse_queues.discard(queue)

        return response

    async def handle_spa_index(self, _request: web.Request) -> web.FileResponse:
        index_file = WEB_DIST_DIR / "index.html"
        return web.FileResponse(index_file)
