"""TeleScraper entrypoint: Web UI server (default) and CLI utilities."""

from __future__ import annotations

import argparse
import webbrowser
from aiohttp import web as aiohttp_web
from rich.console import Console
from rich.panel import Panel

from .db import Database
from .web import WebServer

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="telescraper",
        description="Real-time Telegram group chat to Discord mirror with React Web UI and local SQLite archive.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind the Web UI server (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the Web UI server (default: 8000).",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Automatically open the Web UI in your default browser on startup.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = Database()
    server = WebServer(db=db)

    url = f"http://{args.host}:{args.port}"
    console.print(
        Panel(
            f"[bold green]● TeleScraper Web UI & SQLite Router Running[/bold green]\n\n"
            f"[bold]Web Dashboard:[/bold] [link={url}]{url}[/link]\n"
            f"[bold]Database:[/bold] telescraper.db (auto-imported .env settings)\n"
            f"[bold]Local Media Archive:[/bold] ./media/\n\n"
            f"[dim]Open {url} in your browser to check/uncheck Telegram groups, toggle Discord webhooks, and view archived chats.[/dim]",
            title="TeleScraper",
            border_style="cyan",
        )
    )

    if args.open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        aiohttp_web.run_app(server.app, host=args.host, port=args.port, print=None)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped by user.[/yellow]")


if __name__ == "__main__":
    main()
