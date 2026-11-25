from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from meta_studio.core.fs import finalize_temp_file

APP_NAME = "Meta Studio"
INITIAL_PRESET_FILES: tuple[str, ...] = ("profiles.json", "genres.json")


def is_frozen() -> bool:
    """Devuelve True cuando se ejecuta desde un binario empaquetado con PyInstaller."""
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def get_bundle_root() -> Path:
    """Devuelve la raíz de recursos de solo lectura (PyInstaller `_MEIPASS` o raíz del repo)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


def get_static_dir() -> Path:
    """Resuelve la carpeta de solo lectura `static/` en modo compilado o desarrollo."""
    if is_frozen():
        bundled = get_bundle_root() / "meta_studio" / "web" / "static"
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parent / "web" / "static"


def get_bundled_presets_dir() -> Path:
    """Resuelve la carpeta de plantillas iniciales `presets/` (solo lectura en binario)."""
    return get_bundle_root() / "presets"


def get_user_data_dir() -> Path:
    """Resuelve la carpeta escribible de datos del usuario por sistema operativo.

    - Modo compilado (macOS): `~/Library/Application Support/Meta Studio/`
    - Modo compilado (Windows): `%APPDATA%\\Meta Studio\\`
    - Modo compilado (Linux/otros): `~/.local/share/meta-studio/`
    - Modo desarrollo: `<repo>/presets/`
    """
    if is_frozen():
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / APP_NAME
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA", "").strip()
            base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
            return base / APP_NAME
        xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
        base = Path(xdg_data) if xdg_data else (Path.home() / ".local" / "share")
        return base / "meta-studio"
    return get_bundle_root() / "presets"


def _copy_file_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=dst.parent, prefix=f".tmp_{dst.stem}_", suffix=dst.suffix
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copy2(src, tmp_path)
        finalize_temp_file(tmp_path, dst)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def ensure_user_presets_dir(
    *,
    user_dir: Path | None = None,
    template_dir: Path | None = None,
) -> Path:
    """Garantiza que la carpeta escribible exista y tenga los presets iniciales copiados.

    En modo compilado (o cuando se pasa `user_dir` explícito), crea el directorio y
    copia `profiles.json` y `genres.json` desde las plantillas embebidas únicamente
    si aún no existen (nunca sobrescribe personalizaciones del usuario).
    """
    target = Path(user_dir) if user_dir is not None else get_user_data_dir()
    templates = Path(template_dir) if template_dir is not None else get_bundled_presets_dir()

    if is_frozen() or user_dir is not None:
        target.mkdir(parents=True, exist_ok=True)
        for filename in INITIAL_PRESET_FILES:
            dest_file = target / filename
            src_file = templates / filename
            if not dest_file.exists() and src_file.is_file():
                _copy_file_atomic(src_file, dest_file)

    return target
