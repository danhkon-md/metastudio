from __future__ import annotations

import argparse
import http.client
import os
import socket
import sys
import threading
import time
from collections.abc import Sequence
from urllib.parse import urlparse

import uvicorn

from meta_studio.core.converter import (
    cancel_active_conversions,
    has_active_conversions,
    resolve_ffmpeg_binary,
)
from meta_studio.core.pipeline import (
    cleanup_tracked_orphan_temps,
    has_active_releases,
)
from meta_studio.runtime import ensure_user_presets_dir, get_static_dir, is_frozen
from meta_studio.web.app import create_app

WINDOW_TITLE = "Meta Studio"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 840
WINDOW_MIN_SIZE = (960, 680)
WINDOW_BG_COLOR = "#F6F6F3"


def find_free_port(host: str = "127.0.0.1") -> int:
    """Reserva temporalmente el puerto 0 para obtener un puerto local libre."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def wait_for_health(base_url: str, *, timeout_seconds: float = 10.0) -> bool:
    """Espera hasta que `/api/health` responda HTTP 200 (conexión loopback directa sin proxy)."""
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        conn: http.client.HTTPConnection | None = None
        try:
            conn = http.client.HTTPConnection(host, port, timeout=0.5)
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            resp.read()
            if resp.status == 200:
                return True
        except OSError:
            time.sleep(0.05)
        finally:
            if conn is not None:
                conn.close()
    return False


class LocalUvicornServer:
    """Ejecuta FastAPI/Uvicorn en un hilo daemon con apagado idempotente y limpieza de procesos."""

    def __init__(self, app, *, host: str = "127.0.0.1", port: int | None = None) -> None:
        self.host = host
        self.port = port if port is not None else find_free_port(host)
        self.url = f"http://{self.host}:{self.port}"
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
            loop="asyncio",
            http="h11",
            timeout_graceful_shutdown=5,
        )
        self._server = uvicorn.Server(config)
        # Evitar que Uvicorn intente registrar signal handlers fuera del hilo principal
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._thread = threading.Thread(
            target=self._server.run,
            name="meta-studio-uvicorn",
            daemon=True,
        )
        self._stop_lock = threading.Lock()
        self._stopped = False

    def is_busy(self) -> bool:
        """Indica si existe una exportación o conversión ffmpeg activa."""
        return has_active_releases() or has_active_conversions()

    def start(self, *, timeout_seconds: float = 10.0) -> str:
        self._thread.start()
        if not wait_for_health(self.url, timeout_seconds=timeout_seconds):
            self.stop()
            raise RuntimeError(f"El servidor local no respondió a tiempo en {self.url}")
        return self.url

    def stop(self, *, join_timeout: float = 5.5) -> None:
        """Detiene el servidor de forma idempotente, cancela ffmpeg y barre temporales huérfanos."""
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True

        was_busy = self.is_busy()
        if was_busy:
            cancel_active_conversions()

        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout=join_timeout)

        # Barrer cualquier temporal huérfano si hubo cancelación o cierre abrupto
        cleanup_tracked_orphan_temps()


def _run_desktop_check() -> int:
    import webview  # noqa: F401

    presets_dir = ensure_user_presets_dir()
    static_dir = get_static_dir()
    ffmpeg_path = resolve_ffmpeg_binary()
    print("Meta Studio Desktop check:")
    print(f"  - Frozen mode: {is_frozen()}")
    print(f"  - Static dir: {static_dir} ({'OK' if (static_dir / 'index.html').is_file() else 'MISSING'})")
    print(f"  - User presets dir: {presets_dir}")
    print(f"  - ffmpeg: {ffmpeg_path or 'NOT FOUND'}")
    print("  - pywebview: OK")
    return 0 if (static_dir / "index.html").is_file() and ffmpeg_path else 1


def launch_desktop(*, debug: bool = False) -> int:
    """Levanta FastAPI en un puerto local libre y abre la ventana nativa (macOS/Windows/Linux)."""
    import webview

    # Garantizar que los presets del usuario estén listos antes de crear la app
    presets_dir = ensure_user_presets_dir()
    app = create_app(presets_dir=presets_dir)
    # Mantener app.state.native_pick_folder = None para usar siempre subprocesos seguros en hilos
    app.state.native_pick_folder = None

    local_server = LocalUvicornServer(app)
    url = local_server.start()

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=WINDOW_MIN_SIZE,
        background_color=WINDOW_BG_COLOR,
        text_select=True,
    )

    if window is not None:
        window.events.closed += lambda: local_server.stop()

    try:
        webview.start(debug=debug)
    finally:
        local_server.stop()

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meta-studio-desktop",
        description="Inicia Meta Studio en una ventana nativa de escritorio (macOS, Windows y Linux).",
    )
    parser.add_argument("--debug", action="store_true", help="Habilita el inspector del WebView.")
    parser.add_argument("--check", action="store_true", help="Verifica runtime nativo sin abrir ventana.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        return _run_desktop_check()
    exit_code = launch_desktop(debug=args.debug)
    # En binario compilado, asegurar cierre limpio sin hilos residuales
    if is_frozen():
        os._exit(exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
