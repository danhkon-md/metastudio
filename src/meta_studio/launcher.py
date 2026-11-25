from __future__ import annotations

import argparse
import http.client
import sys
import threading
import time
import webbrowser
from collections.abc import Sequence

import uvicorn

from meta_studio.core.converter import resolve_ffmpeg_binary
from meta_studio.runtime import get_static_dir
from meta_studio.services.presets import PresetStore
from meta_studio.web.app import create_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _wait_and_open_browser(host: str, port: int, timeout_seconds: float = 10.0) -> None:
    url = f"http://{host}:{port}"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        conn: http.client.HTTPConnection | None = None
        try:
            conn = http.client.HTTPConnection(host, port, timeout=0.5)
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            resp.read()
            if resp.status == 200:
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.15)
        finally:
            if conn is not None:
                conn.close()


def run_check() -> int:
    """Verifica que la app FastAPI, assets estáticos, presets y ffmpeg estén listos."""
    app = create_app()
    store = PresetStore()
    profiles = store.load_profiles()
    genres = store.load_genres()
    ffmpeg_path = resolve_ffmpeg_binary()
    static_dir = get_static_dir()

    required_static = ["index.html", "styles.css", "app.js"]
    missing_static = [name for name in required_static if not (static_dir / name).is_file()]

    print("Meta Studio check:")
    print(f"  - FastAPI app: OK ({app.title} v{app.version})")
    print(f"  - Static assets: {'OK' if not missing_static else f'MISSING {missing_static}'}")
    print(f"  - Profiles loaded: {len(profiles)}")
    print(f"  - Genres tags loaded: {len(genres.tags)} (distributor: {len(genres.distributor_primary)})")
    print(f"  - ffmpeg available: {f'OK ({ffmpeg_path})' if ffmpeg_path else 'NOT FOUND'}")

    if missing_static or not ffmpeg_path:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meta-studio",
        description="Inicia Meta Studio localmente y abre la interfaz web.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Host local (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Puerto local (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="No abrir automáticamente el navegador al iniciar.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verifica la app, assets, presets y ffmpeg sin levantar el servidor.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        return run_check()

    app = create_app()
    url = f"http://{args.host}:{args.port}"
    print(f"Meta Studio iniciando en {url} (Ctrl+C para salir)")

    if not args.no_browser:
        opener = threading.Thread(
            target=_wait_and_open_browser,
            args=(args.host, args.port),
            daemon=True,
        )
        opener.start()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        loop="asyncio",
        http="h11",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
