from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Literal

TargetConvertedFormat = Literal["flac", "mp3_320"]

FALLBACK_FFMPEG_PATHS: tuple[Path, ...] = (
    Path("/opt/homebrew/bin/ffmpeg"),
    Path("/usr/local/bin/ffmpeg"),
)

DEFAULT_FFMPEG_TIMEOUT_SECONDS = 300.0

_FFMPEG_LOCK = threading.Lock()
_ACTIVE_FFMPEG_PROCS: set[subprocess.Popen[str]] = set()
_CANCEL_EVENT = threading.Event()


class ConversionError(RuntimeError):
    """Error al convertir audio WAV mediante ffmpeg."""


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    """Termina limpiamente el proceso o grupo de procesos de ffmpeg (POSIX y Windows)."""
    if proc.poll() is not None:
        return
    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                return
    else:
        try:
            proc.terminate()
        except OSError:
            return

    try:
        proc.wait(timeout=1.5)
        return
    except subprocess.TimeoutExpired:
        pass

    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
    else:
        try:
            proc.kill()
        except OSError:
            pass

    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def cancel_active_conversions() -> int:
    """Señala cancelación y termina todos los procesos ffmpeg activos."""
    _CANCEL_EVENT.set()
    with _FFMPEG_LOCK:
        procs = list(_ACTIVE_FFMPEG_PROCS)
    for proc in procs:
        _terminate_process_group(proc)
    return len(procs)


def reset_cancellation() -> None:
    """Limpia la señal de cancelación antes de iniciar un nuevo procesamiento."""
    _CANCEL_EVENT.clear()


def has_active_conversions() -> bool:
    """Indica si hay procesos ffmpeg corriendo actualmente."""
    with _FFMPEG_LOCK:
        return any(proc.poll() is None for proc in _ACTIVE_FFMPEG_PROCS)


def _resolve_imageio_ffmpeg() -> str | None:
    """Obtiene el binario embebido provisto por `imageio-ffmpeg` como último fallback."""
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    if exe:
        exe_path = Path(exe)
        if exe_path.is_file() and (os.name == "nt" or os.access(exe_path, os.X_OK)):
            return str(exe_path)
    return None


def resolve_ffmpeg_binary(
    ffmpeg_bin: str = "ffmpeg",
    *,
    fallback_paths: tuple[Path, ...] | None = None,
    allow_imageio_fallback: bool = True,
    prefer_bundled: bool | None = None,
) -> str | None:
    """Resuelve la ruta ejecutable de ffmpeg con prioridad multiplataforma.

    - En modo desarrollo (`prefer_bundled=False`):
      1. `shutil.which(ffmpeg_bin)` en PATH.
      2. `/opt/homebrew/bin/ffmpeg` y `/usr/local/bin/ffmpeg` (macOS o `fallback_paths`).
      3. Fallback automático con `imageio_ffmpeg.get_ffmpeg_exe()`.
    - En binario compilado (`is_frozen()` / `prefer_bundled=True`):
      prioriza el binario embebido de `imageio-ffmpeg` dentro del propio bundle
      antes de caer a rutas externas del sistema.
    """
    from meta_studio.runtime import is_frozen

    use_bundled_first = is_frozen() if prefer_bundled is None else prefer_bundled

    if ffmpeg_bin == "ffmpeg" and use_bundled_first and allow_imageio_fallback:
        bundled = _resolve_imageio_ffmpeg()
        if bundled:
            return bundled

    found = shutil.which(ffmpeg_bin)
    if found:
        return found

    if ffmpeg_bin == "ffmpeg":
        candidates = (
            fallback_paths
            if fallback_paths is not None
            else (FALLBACK_FFMPEG_PATHS if sys.platform == "darwin" else ())
        )
        for candidate in candidates:
            if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
                return str(candidate)

        if allow_imageio_fallback:
            return _resolve_imageio_ffmpeg()
    else:
        explicit = Path(ffmpeg_bin)
        if explicit.is_file() and (os.name == "nt" or os.access(explicit, os.X_OK)):
            return str(explicit)

    return None


def is_ffmpeg_available(ffmpeg_bin: str = "ffmpeg") -> bool:
    """Comprueba si ffmpeg está disponible en PATH, Homebrew o `imageio-ffmpeg`."""
    return resolve_ffmpeg_binary(ffmpeg_bin) is not None


def build_ffmpeg_command(
    source_path: Path,
    target_format: TargetConvertedFormat,
    output_path: Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Construye el comando ffmpeg para convertir WAV -> FLAC o MP3 320k.

    Incluye siempre `-map_metadata -1 -vn` para eliminar cualquier metadata
    o stream de video/portada basura proveniente del DAW.
    """
    base_cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-map_metadata",
        "-1",
        "-vn",
    ]
    if target_format == "flac":
        return [*base_cmd, "-c:a", "flac", str(output_path)]
    if target_format == "mp3_320":
        return [
            *base_cmd,
            "-c:a",
            "libmp3lame",
            "-b:a",
            "320k",
            "-write_id3v1",
            "0",
            "-id3v2_version",
            "4",
            str(output_path),
        ]
    raise ConversionError(f"Formato de conversión no soportado: {target_format!r}")


def convert_wav(
    source_path: Path,
    target_format: TargetConvertedFormat,
    dest_temp_path: Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
    timeout_seconds: float = DEFAULT_FFMPEG_TIMEOUT_SECONDS,
) -> Path:
    """Convierte un archivo WAV de origen a FLAC o MP3 320k en un archivo temporal.

    - Regla Copy-First: nunca modifica el archivo origen.
    - Usa grupo de procesos aislado + timeout y mata el proceso si se cancela.
    """
    source_path = Path(source_path)
    dest_temp_path = Path(dest_temp_path)

    if _CANCEL_EVENT.is_set():
        raise ConversionError("Conversión cancelada antes de iniciar")
    if not source_path.is_file():
        raise ConversionError(f"Archivo de origen no encontrado: {source_path}")
    if source_path.suffix.lower() != ".wav":
        raise ConversionError(
            f"La conversión con ffmpeg solo está permitida desde WAV, recibido: {source_path.suffix!r}"
        )
    if source_path.resolve() == dest_temp_path.resolve():
        raise ConversionError("El archivo temporal de salida no puede coincidir con el origen")

    resolved_bin = resolve_ffmpeg_binary(ffmpeg_bin) or ffmpeg_bin
    cmd = build_ffmpeg_command(
        source_path, target_format, dest_temp_path, ffmpeg_bin=resolved_bin
    )

    popen_kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
        popen_kwargs["creationflags"] = creation_flags
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[arg-type]
    except FileNotFoundError as exc:
        raise ConversionError(f"No se encontró el binario de ffmpeg: {ffmpeg_bin!r}") from exc

    with _FFMPEG_LOCK:
        _ACTIVE_FFMPEG_PROCS.add(proc)

    deadline = time.monotonic() + timeout_seconds
    stderr_out = ""
    try:
        while True:
            if _CANCEL_EVENT.is_set():
                _terminate_process_group(proc)
                raise ConversionError(f"Conversión cancelada para {source_path.name}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_group(proc)
                raise ConversionError(
                    f"ffmpeg excedió el tiempo límite ({timeout_seconds:.0f}s) para {source_path.name}"
                )
            try:
                _stdout, stderr_out = proc.communicate(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        if proc.returncode != 0:
            raise ConversionError(
                f"ffmpeg falló al convertir {source_path.name} a {target_format}: {(stderr_out or '').strip()}"
            )
    except Exception:
        _terminate_process_group(proc)
        raise
    finally:
        with _FFMPEG_LOCK:
            _ACTIVE_FFMPEG_PROCS.discard(proc)

    if not dest_temp_path.is_file() or dest_temp_path.stat().st_size == 0:
        raise ConversionError(
            f"ffmpeg no generó un archivo válido en {dest_temp_path}"
        )
    return dest_temp_path
