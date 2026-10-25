# TeleScraper

Real-time Telegram group chat to Discord mirror and local SQLite archive with a **React + Vite** Web UI, powered by **Telethon**, **aiohttp**, and **uv**.

## Key Features
- **Automatic `.env` to SQLite Migration**: On startup, TeleScraper automatically imports your `TG_API_ID`, `TG_API_HASH`, `DISCORD_WEBHOOK_URL`, and `SOURCE_CHAT` from `.env` into `telescraper.db`.
- **Checkbox Group Routing**: Check or uncheck any number of joined Telegram groups/channels in the Web UI to start or stop capturing live messages immediately.
- **Global & Per-Group Discord Webhook Toggle**: Pause or resume Discord Webhook forwarding globally with one click in the top bar, or toggle Discord forwarding and set custom Webhook URLs per group.
- **Local Media Archive & Chat Viewer**: Saves photos, videos, audio/voice notes, stickers, and documents locally in `./media/` and displays them inline inside the Web UI chat viewer alongside Discord delivery status (`Sent`, `Paused`, `Failed` with one-click retry).
- **Catppuccin Peach Themes (WCAG AA Verified)**: Switch between **Catppuccin Mocha (Peach)** and **Catppuccin Latte (Light Peach)** in the header.

## Quick Start

Start the Web UI and background Telegram listener with:

```bash
uv run mirror.py
```
*(Or `uv run telescraper`, or `uv run mirror.py --open-browser`)*

Then open **`http://127.0.0.1:8000`** in your browser.

### Rebuilding the Frontend (If Editing `web/src/`)
The compiled React + Vite bundle in `web/dist/` is served automatically by `uv run mirror.py`. If you modify the frontend source files in `web/src/`:

```bash
npm --prefix web run build
```
Or for live frontend development with HMR on port `5173` (proxying `/api` and `/media` to `127.0.0.1:8000`):
```bash
npm --prefix web run dev
```
