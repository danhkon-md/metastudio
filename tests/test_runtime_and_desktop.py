from __future__ import annotations

import http.client
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from meta_studio.core.converter import is_ffmpeg_available, resolve_ffmpeg_binary
from meta_studio.desktop import LocalUvicornServer, find_free_port
from meta_studio.runtime import (
    ensure_user_presets_dir,
    get_bundled_presets_dir,
    get_static_dir,
    get_user_data_dir,
    is_frozen,
)
from meta_studio.web import app as web_app_module
from meta_studio.web.app import create_app


def test_runtime_dev_mode_paths() -> None:
    assert is_frozen() is False
    static_dir = get_static_dir()
    assert (static_dir / "index.html").is_file()
    presets_dir = get_bundled_presets_dir()
    assert (presets_dir / "profiles.json").is_file()
    assert (presets_dir / "genres.json").is_file()
    assert get_user_data_dir() == presets_dir


def test_runtime_frozen_mode_paths_and_non_destructive_seeding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_meipass = tmp_path / "bundle_meipass"
    fake_static = fake_meipass / "meta_studio" / "web" / "static"
    fake_static.mkdir(parents=True)
    (fake_static / "index.html").write_text("<h1>Frozen</h1>", encoding="utf-8")

    fake_templates = fake_meipass / "presets"
    fake_templates.mkdir(parents=True)
    (fake_templates / "profiles.json").write_text('{"profiles": []}\n', encoding="utf-8")
    (fake_templates / "genres.json").write_text(
        '{"distributor_primary": ["Electronic"], "tags": ["Ambient"]}\n',
        encoding="utf-8",
    )

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    assert is_frozen() is True
    assert get_static_dir() == fake_static
    assert get_bundled_presets_dir() == fake_templates

    expected_user_dir = fake_home / "Library" / "Application Support" / "Meta Studio"
    assert get_user_data_dir() == expected_user_dir

    # Primer arranque: crea carpeta y copia presets iniciales
    seeded_dir = ensure_user_presets_dir()
    assert seeded_dir == expected_user_dir
    assert (seeded_dir / "profiles.json").is_file()
    assert (seeded_dir / "genres.json").is_file()

    # Personalización del usuario: nunca debe ser sobrescrita en arranques posteriores
    custom_profiles = '{"profiles": [{"id": "custom"}]}\n'
    (seeded_dir / "profiles.json").write_text(custom_profiles, encoding="utf-8")
    ensure_user_presets_dir()
    assert (seeded_dir / "profiles.json").read_text(encoding="utf-8") == custom_profiles


def test_resolve_ffmpeg_uses_homebrew_fallbacks_when_path_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_hb_ffmpeg = tmp_path / "opt_homebrew_ffmpeg"
    fake_hb_ffmpeg.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_hb_ffmpeg.chmod(fake_hb_ffmpeg.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr("meta_studio.core.converter.shutil.which", lambda _cmd: None)

    resolved = resolve_ffmpeg_binary(
        "ffmpeg",
        fallback_paths=(tmp_path / "missing_ffmpeg", fake_hb_ffmpeg),
        allow_imageio_fallback=False,
    )
    assert resolved == str(fake_hb_ffmpeg)

    # Si no existe en PATH ni en fallbacks ni imageio-ffmpeg, devuelve None
    assert (
        resolve_ffmpeg_binary(
            "ffmpeg",
            fallback_paths=(tmp_path / "missing_1",),
            allow_imageio_fallback=False,
        )
        is None
    )

    monkeypatch.setattr(
        "meta_studio.core.converter.FALLBACK_FFMPEG_PATHS",
        (tmp_path / "missing_1",),
    )
    monkeypatch.setattr(
        "meta_studio.core.converter._resolve_imageio_ffmpeg",
        lambda: None,
    )
    assert is_ffmpeg_available() is False


def test_resolve_ffmpeg_priority_and_imageio_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_hb = tmp_path / "hb_ffmpeg"
    fake_hb.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_hb.chmod(fake_hb.stat().st_mode | stat.S_IXUSR)

    fake_imageio = tmp_path / "imageio_ffmpeg_bin"
    fake_imageio.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_imageio.chmod(fake_imageio.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("meta_studio.core.converter.FALLBACK_FFMPEG_PATHS", (fake_hb,))

    # 1. En dev: PATH tiene prioridad #1 sobre Homebrew e imageio-ffmpeg
    monkeypatch.setattr(
        "meta_studio.core.converter.shutil.which",
        lambda _cmd: "/custom/path/ffmpeg",
    )
    assert resolve_ffmpeg_binary("ffmpeg") == "/custom/path/ffmpeg"

    # 2. En dev sin PATH: Homebrew tiene prioridad #2 sobre imageio-ffmpeg
    monkeypatch.setattr("meta_studio.core.converter.shutil.which", lambda _cmd: None)
    assert resolve_ffmpeg_binary("ffmpeg") == str(fake_hb)

    # 3. En dev sin PATH ni Homebrew: cae al fallback #3 real de imageio-ffmpeg
    monkeypatch.setattr("meta_studio.core.converter.FALLBACK_FFMPEG_PATHS", ())
    resolved_real = resolve_ffmpeg_binary("ffmpeg")
    assert resolved_real is not None
    assert Path(resolved_real).is_file()
    assert "imageio_ffmpeg" in resolved_real or "ffmpeg" in Path(resolved_real).name.lower()

    # 4. En modo frozen: prioriza el binario embebido de imageio-ffmpeg sobre PATH/Homebrew
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(
        "meta_studio.core.converter.shutil.which",
        lambda _cmd: "/opt/homebrew/bin/ffmpeg",
    )
    monkeypatch.setattr("meta_studio.core.converter.FALLBACK_FFMPEG_PATHS", (fake_hb,))
    monkeypatch.setattr(
        "meta_studio.core.converter._resolve_imageio_ffmpeg",
        lambda: str(fake_imageio),
    )
    assert resolve_ffmpeg_binary("ffmpeg") == str(fake_imageio)


def test_runtime_user_data_dir_per_os(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    # macOS
    monkeypatch.setattr(sys, "platform", "darwin")
    assert get_user_data_dir() == fake_home / "Library" / "Application Support" / "Meta Studio"

    # Windows con %APPDATA%
    fake_appdata = str(tmp_path / "AppDataRoaming")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", fake_appdata)
    assert get_user_data_dir() == Path(fake_appdata) / "Meta Studio"

    # Linux sin XDG_DATA_HOME -> ~/.local/share/meta-studio
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert get_user_data_dir() == fake_home / ".local" / "share" / "meta-studio"

    # Linux con XDG_DATA_HOME
    fake_xdg = str(tmp_path / "xdg_share")
    monkeypatch.setenv("XDG_DATA_HOME", fake_xdg)
    assert get_user_data_dir() == Path(fake_xdg) / "meta-studio"


def test_cross_platform_pick_and_open_folder_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows PowerShell picker: verifica preámbulo UTF-8, encoding/errors y ruta con acentos
    captured_win_calls: list[tuple[list[str], dict[str, object]]] = []
    utf8_win_path = r"C:\Usuarios\Música\Álbum Español"

    def _fake_win_run(cmd, **kwargs):
        captured_win_calls.append((list(cmd), dict(kwargs)))
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{utf8_win_path}\n", stderr="")

    monkeypatch.setattr(web_app_module.sys, "platform", "win32")
    monkeypatch.setattr(
        web_app_module.shutil,
        "which",
        lambda name: "powershell.exe" if name == "powershell" else None,
    )
    monkeypatch.setattr(web_app_module.subprocess, "run", _fake_win_run)

    win_pick = web_app_module._run_pick_folder(initial_dir=str(tmp_path))
    assert win_pick["selected"] is True
    assert win_pick["path"] == utf8_win_path
    assert len(captured_win_calls) == 1
    win_cmd, win_kwargs = captured_win_calls[0]
    assert "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" in win_cmd[-1]
    assert "$OutputEncoding=[System.Text.Encoding]::UTF8;" in win_cmd[-1]
    assert win_kwargs["encoding"] == "utf-8"
    assert win_kwargs["errors"] == "replace"

    # Windows open-folder via os.startfile
    started: list[str] = []
    monkeypatch.setattr(
        web_app_module.os,
        "startfile",
        lambda path: started.append(path),
        raising=False,
    )
    web_app_module._open_in_file_manager(tmp_path)
    assert started == [str(tmp_path)]

    # Linux picker sin zenity/kdialog -> fallback manual limpio
    monkeypatch.setattr(web_app_module.sys, "platform", "linux")
    monkeypatch.setattr(web_app_module.shutil, "which", lambda _name: None)
    linux_fallback = web_app_module._run_pick_folder()
    assert linux_fallback["selected"] is False
    assert linux_fallback["supported"] is False

    # Linux open-folder via xdg-open con ruta resuelta
    linux_cmds: list[list[str]] = []
    monkeypatch.setattr(
        web_app_module.subprocess,
        "run",
        lambda cmd, **kwargs: (
            linux_cmds.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )
    web_app_module._open_in_file_manager(tmp_path)
    assert linux_cmds == [["xdg-open", str(tmp_path.resolve())]]


def test_find_free_port_and_local_uvicorn_server_clean_shutdown(tmp_path: Path) -> None:
    port = find_free_port()
    assert 1024 <= port <= 65535

    app = create_app(presets_dir=tmp_path / "presets")
    server = LocalUvicornServer(app)
    url = server.start(timeout_seconds=5.0)
    try:
        parsed = urlparse(url)
        conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=2.0)
        try:
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            assert resp.status == 200
            body = json.loads(resp.read().decode("utf-8"))
            assert body["status"] == "ok"
            assert body["version"] == "1.0.0"
        finally:
            conn.close()
    finally:
        server.stop(join_timeout=3.0)

    assert not server._thread.is_alive()


def test_native_pick_folder_hook_persists_selected_dir(tmp_path: Path) -> None:
    presets_dir = tmp_path / "presets"
    chosen_dir = tmp_path / "chosen_exports"
    chosen_dir.mkdir()

    app = create_app(presets_dir=presets_dir)
    app.state.native_pick_folder = lambda _initial: {
        "selected": True,
        "path": str(chosen_dir),
        "cancelled": False,
        "supported": True,
    }
    client = TestClient(app)

    res = client.post("/api/pick-folder", json={})
    assert res.status_code == 200
    assert res.json()["path"] == str(chosen_dir)
    assert client.get("/api/settings/output-dir").json()["output_dir"] == str(chosen_dir)
    assert os.path.isfile(presets_dir / "settings.json")
