from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

DEFAULT_FILE_MODE = 0o644
ORPHAN_TEMP_PREFIXES: tuple[str, ...] = (".tmp_meta_", ".tmp_cover_")


def fsync_file(path: Path) -> None:
    """Fuerza la escritura a disco (`os.fsync`) de un archivo existente (POSIX y Windows)."""
    if os.name == "nt":
        with Path(path).open("r+b") as fh:
            os.fsync(fh.fileno())
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def finalize_temp_file(
    tmp_path: Path,
    dest_path: Path,
    *,
    mode: int = DEFAULT_FILE_MODE,
) -> Path:
    """Sincroniza a disco, ajusta permisos legibles (`0o644`) y mueve atómicamente."""
    tmp_path = Path(tmp_path)
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fsync_file(tmp_path)
    os.chmod(tmp_path, mode)
    os.replace(tmp_path, dest_path)
    return dest_path


def cleanup_orphan_temps(directories: Iterable[Path]) -> int:
    """Elimina archivos temporales `.tmp_meta_*` y `.tmp_cover_*` huérfanos."""
    removed = 0
    seen: set[Path] = set()
    for directory in directories:
        dir_path = Path(directory)
        if dir_path in seen or not dir_path.is_dir():
            continue
        seen.add(dir_path)
        for entry in dir_path.iterdir():
            if entry.is_file() and entry.name.startswith(ORPHAN_TEMP_PREFIXES):
                try:
                    entry.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass
    return removed
