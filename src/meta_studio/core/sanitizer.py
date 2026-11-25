from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from meta_studio.core.models import ReleaseStatus

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_FORBIDDEN_CHARS_RE = re.compile(r'[/\\*?"<>|]')
_MULTI_SPACE_RE = re.compile(r"\s+")
MAX_COMPONENT_UTF8_BYTES = 180
MAX_SAFE_FULL_PATH_CHARS = 240

_WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def _truncate_utf8_nfc(text: str, max_bytes: int) -> str:
    """Recorta `text` a <= `max_bytes` bytes UTF-8 sin romper secuencias ni NFC."""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    out_chars: list[str] = []
    used_bytes = 0
    for ch in text:
        ch_bytes = len(ch.encode("utf-8"))
        if used_bytes + ch_bytes > max_bytes:
            break
        out_chars.append(ch)
        used_bytes += ch_bytes
    return unicodedata.normalize("NFC", "".join(out_chars))


def _truncate_chars_nfc(text: str, max_chars: int) -> str:
    """Recorta `text` a <= `max_chars` caracteres conservando NFC."""
    if len(text) <= max_chars:
        return text
    return unicodedata.normalize("NFC", text[:max_chars])


def _escape_windows_reserved(name: str) -> str:
    """Prefija con `_` los nombres reservados de dispositivo en Windows (CON, NUL, COM1, etc.)."""
    stem = name.split(".", 1)[0].rstrip(" ").upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        return f"_{name}"
    return name


def sanitize_name(
    name: str,
    *,
    max_bytes: int = MAX_COMPONENT_UTF8_BYTES,
    max_chars: int | None = None,
    fallback: str | None = None,
) -> str:
    """Sanitiza un segmento de carpeta o nombre de archivo compatible con macOS, Linux y Windows.

    Reglas:
    - Normalización Unicode NFC.
    - Eliminación de caracteres de control (`\\x00-\\x1f`, `\\x7f`).
    - Reemplazo de ':' por ' -'.
    - Eliminación de / \\ * ? " < > |.
    - Colapso de espacios múltiples.
    - Eliminación de puntos y espacios al final (y espacios al inicio).
    - Protección contra nombres reservados de Windows (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`9`, `LPT1`-`9`).
    - Límite seguro en bytes UTF-8 (`max_bytes`, por defecto 180) y opcionalmente `max_chars`.
    """
    normalized = unicodedata.normalize("NFC", name)
    normalized = _CONTROL_CHARS_RE.sub("", normalized)
    normalized = normalized.replace(":", " -")
    normalized = _FORBIDDEN_CHARS_RE.sub("", normalized)
    normalized = _MULTI_SPACE_RE.sub(" ", normalized)
    normalized = normalized.lstrip(" ").rstrip(". ")
    if normalized:
        normalized = _escape_windows_reserved(normalized)
    if max_bytes > 0 and normalized:
        normalized = _truncate_utf8_nfc(normalized, max_bytes).lstrip(" ").rstrip(". ")
    if max_chars is not None and max_chars > 0 and normalized:
        normalized = _truncate_chars_nfc(normalized, max_chars).lstrip(" ").rstrip(". ")
    if normalized:
        normalized = _escape_windows_reserved(normalized)
    if not normalized:
        if fallback is not None:
            return sanitize_name(fallback, max_bytes=max_bytes, max_chars=max_chars, fallback=None)
        raise ValueError(f"El nombre sanitizado resultó vacío para la entrada: {name!r}")
    return normalized


def build_album_dir(
    output_dir: Path,
    album_artist: str,
    year: str,
    album: str,
    status: ReleaseStatus = "Final",
    *,
    max_full_path_chars: int = MAX_SAFE_FULL_PATH_CHARS,
) -> Path:
    """Construye la ruta destino del álbum:
    `{output_dir}/{AlbumArtist}/[{Year}] {Album}/`
    añadiendo ` [{Status}]` únicamente si status es `Demo` o `WIP`,
    acotando componentes si la ruta combinada podría acercarse a `MAX_PATH` en Windows.
    """
    base = Path(output_dir)
    artist_folder = sanitize_name(album_artist)
    sanitized_album = sanitize_name(album)
    suffix = f" [{status}]" if status in ("Demo", "WIP") else ""
    album_folder = sanitize_name(f"[{year}] {sanitized_album}{suffix}")

    # Reservar al menos 60 caracteres para los nombres de pistas dentro de la carpeta del álbum
    candidate = base / artist_folder / album_folder
    max_dir_len = max_full_path_chars - 60
    if len(str(candidate)) > max_dir_len:
        available = max(24, max_dir_len - len(str(base)) - 2)
        artist_budget = max(12, available // 3)
        album_budget = max(12, available - artist_budget)
        artist_folder = sanitize_name(artist_folder, max_chars=artist_budget)
        prefix_year = f"[{year}] "
        inner_album_budget = max(6, album_budget - len(prefix_year) - len(suffix))
        short_album = sanitize_name(sanitized_album, max_chars=inner_album_budget)
        album_folder = sanitize_name(f"{prefix_year}{short_album}{suffix}")
        candidate = base / artist_folder / album_folder

    return candidate


def build_track_filename(
    track_number: int,
    title: str,
    extension: str,
    *,
    disc_number: int = 1,
    total_discs: int = 1,
    parent_dir: Path | None = None,
    max_full_path_chars: int = MAX_SAFE_FULL_PATH_CHARS,
) -> str:
    """Construye el nombre del archivo de pista sanitizado en Unicode NFC:
    - 1 disco: `{track:02d} - {Title}.{ext}`
    - >1 disco: `{disc}-{track:02d} - {Title}.{ext}`
    """
    if track_number < 1:
        raise ValueError("track_number debe ser >= 1")
    if disc_number < 1:
        raise ValueError("disc_number debe ser >= 1")
    if total_discs < 1:
        raise ValueError("total_discs debe ser >= 1")

    clean_ext = extension.lstrip(".").strip().lower()
    if not clean_ext:
        raise ValueError("extension no puede estar vacía")

    sanitized_title = sanitize_name(title)
    if total_discs > 1:
        prefix = f"{disc_number}-{track_number:02d} - "
    else:
        prefix = f"{track_number:02d} - "

    ext_part = f".{clean_ext}"
    reserved_bytes = len(prefix.encode("utf-8")) + len(ext_part.encode("utf-8"))
    title_byte_budget = max(16, MAX_COMPONENT_UTF8_BYTES - reserved_bytes)

    title_char_budget: int | None = None
    if parent_dir is not None:
        remaining_chars = (
            max_full_path_chars
            - len(str(parent_dir))
            - 1
            - len(prefix)
            - len(ext_part)
        )
        title_char_budget = max(12, remaining_chars)

    sanitized_title = sanitize_name(
        sanitized_title,
        max_bytes=title_byte_budget,
        max_chars=title_char_budget,
    )
    stem = f"{prefix}{sanitized_title}"

    return unicodedata.normalize("NFC", f"{stem}{ext_part}")
