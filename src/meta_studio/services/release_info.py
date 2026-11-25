from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from meta_studio.core.fs import finalize_temp_file
from meta_studio.core.models import (
    DistributionInfo,
    ReleaseInput,
    ReleaseStatus,
    SupportedFormat,
    TrackProcessResult,
)

_EMPTY_MARKER = "—"
_ESCAPED_EMPTY_MARKER = "\\—"
_NO_CREDITS_MARKER = "_Sin créditos ni notas._"


def _write_text_atomic(path: Path, content: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".tmp_{path.stem}_", suffix=path.suffix
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
        return finalize_temp_file(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def format_duration(seconds: float | None) -> str:
    """Formatea una duración en segundos como MM:SS(.cc) legible."""
    if seconds is None:
        return _EMPTY_MARKER
    rounded = round(float(seconds), 2)
    minutes = int(rounded // 60)
    rem = rounded - (minutes * 60)
    if abs(rem - round(rem)) < 1e-6:
        return f"{minutes:02d}:{int(round(rem)):02d}"
    return f"{minutes:02d}:{rem:05.2f}"


def parse_duration(raw: str) -> float | None:
    """Parsea MM:SS, MM:SS.cc o HH:MM:SS(.cc) de vuelta a segundos."""
    cleaned = raw.strip()
    if not cleaned or cleaned == _EMPTY_MARKER:
        return None
    parts = cleaned.split(":")
    if len(parts) == 2:
        minutes = int(parts[0])
        secs = float(parts[1])
        return round(minutes * 60 + secs, 2)
    if len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        secs = float(parts[2])
        return round(hours * 3600 + minutes * 60 + secs, 2)
    raise ValueError(f"Formato de duración no reconocido: {raw!r}")


def _escape_inline(value: str | None) -> str:
    """Escapa un valor de una sola línea protegiendo `—` literal y saltos de línea."""
    if value is None or value == "":
        return _EMPTY_MARKER
    escaped = value.replace("\\", "\\\\").replace("\r\n", "\n").replace("\n", "\\n")
    if escaped == _EMPTY_MARKER:
        return _ESCAPED_EMPTY_MARKER
    return escaped


def _unescape_inline(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned or cleaned == _EMPTY_MARKER:
        return None
    if cleaned == _ESCAPED_EMPTY_MARKER:
        return _EMPTY_MARKER
    # Unescape secuencial (\\ -> \, \n -> newline, \| -> |)
    out: list[str] = []
    idx = 0
    length = len(cleaned)
    while idx < length:
        ch = cleaned[idx]
        if ch == "\\" and idx + 1 < length:
            nxt = cleaned[idx + 1]
            if nxt == "n":
                out.append("\n")
                idx += 2
                continue
            if nxt in ("\\", "|", _EMPTY_MARKER):
                out.append(nxt)
                idx += 2
                continue
        out.append(ch)
        idx += 1
    return "".join(out)


def _escape_cell(value: str | None) -> str:
    if value is None or value == "":
        return _EMPTY_MARKER
    escaped = _escape_inline(value)
    return escaped.replace("|", "\\|")


def _unescape_cell(raw: str) -> str | None:
    return _unescape_inline(raw)


def _split_md_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]


def _format_bool(value: bool) -> str:
    return "Sí" if value else "No"


def _parse_bool(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"sí", "si", "yes", "true", "1"}


def _format_tag_list(tags: list[str]) -> str:
    return ", ".join(f"`{tag}`" for tag in tags)


def _parse_tag_list(raw: str) -> list[str]:
    backticked = re.findall(r"`([^`]+)`", raw)
    if backticked:
        return [item.strip() for item in backticked if item.strip()]
    return [item.strip() for item in raw.split(",") if item.strip()]


def _choose_code_fence(text: str) -> str:
    """Elige un fence de backticks más largo que cualquier secuencia dentro de `text`."""
    runs = re.findall(r"`+", text)
    max_run = max((len(r) for r in runs), default=0)
    return "`" * max(3, max_run + 1)


class ReleaseInfoTrack(BaseModel):
    """Entrada de pista persistida en `release-info.json` y `release-info.md`."""

    model_config = ConfigDict(extra="forbid")

    disc_number: int = Field(default=1, ge=1)
    track_number: int = Field(ge=1)
    total_tracks: int = Field(ge=1)
    title: str
    artist: str
    genre: list[str] = Field(default_factory=list)
    duration_seconds: float | None = None
    format: SupportedFormat | None = None
    filename: str | None = None
    isrc: str | None = None
    verified: bool | None = None
    has_cover: bool | None = None
    id3_version: str | None = None
    verification_notes: str | None = None


class ReleaseInfoDocument(BaseModel):
    """Documento estructurado completo de un lanzamiento (JSON + Markdown)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    album: str
    album_artist: str
    artist: str
    release_type: str
    status: ReleaseStatus = "Final"
    date: str
    original_date: str
    year: str
    genre: list[str]
    label: str | None = None
    catalog_number: str | None = None
    copyright: str | None = None
    distribution: DistributionInfo = Field(default_factory=DistributionInfo)
    cover_file: str | None = None
    all_verified: bool | None = None
    tracks: list[ReleaseInfoTrack] = Field(default_factory=list)


def build_release_info(
    release: ReleaseInput,
    track_results: list[TrackProcessResult],
    *,
    cover_path: Path | None = None,
) -> ReleaseInfoDocument:
    """Construye un `ReleaseInfoDocument` a partir de un lanzamiento procesado."""
    assert release.original_date is not None
    assert release.year is not None

    info_tracks: list[ReleaseInfoTrack] = []
    for item in track_results:
        id3_ver = (
            ".".join(str(part) for part in item.verification.id3_version)
            if item.verification.id3_version is not None
            else None
        )
        notes = (
            "; ".join(
                f"{m.field}: esperado={m.expected!r}, leído={m.actual!r}"
                for m in item.verification.mismatches
            )
            if item.verification.mismatches
            else None
        )
        info_tracks.append(
            ReleaseInfoTrack(
                disc_number=item.metadata.disc_number,
                track_number=item.metadata.track_number,
                total_tracks=item.metadata.total_tracks,
                title=item.metadata.title,
                artist=item.metadata.artist,
                genre=list(item.metadata.genre),
                duration_seconds=round(item.verification.duration_seconds, 2),
                format=item.format,
                filename=item.output_path.name,
                isrc=item.isrc,
                verified=item.verification.passed,
                has_cover=item.verification.has_cover,
                id3_version=id3_ver,
                verification_notes=notes,
            )
        )

    all_verified = all(item.verification.passed for item in track_results) if track_results else None
    return ReleaseInfoDocument(
        album=release.album,
        album_artist=release.album_artist,
        artist=release.artist or release.album_artist,
        release_type=release.release_type,
        status=release.status,
        date=release.date,
        original_date=release.original_date,
        year=release.year,
        genre=list(release.genre),
        label=release.label,
        catalog_number=release.catalog_number,
        copyright=release.copyright,
        distribution=release.distribution,
        cover_file=cover_path.name if cover_path is not None else None,
        all_verified=all_verified,
        tracks=info_tracks,
    )


def render_release_info_json(doc: ReleaseInfoDocument) -> str:
    """Serializa `ReleaseInfoDocument` a JSON UTF-8 formateado."""
    return json.dumps(doc.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def parse_release_info_json(content: str) -> ReleaseInfoDocument:
    """Deserializa un texto JSON de `release-info.json` en `ReleaseInfoDocument`."""
    return ReleaseInfoDocument.model_validate(json.loads(content))


def render_release_info_md(doc: ReleaseInfoDocument) -> str:
    """Genera `release-info.md` legible para Bandcamp/DistroKid y reimportable sin pérdida."""
    dist = doc.distribution
    lines: list[str] = [
        f"# {doc.album} — {doc.album_artist}",
        "",
        "## Identidad",
        f"- **Album:** {_escape_inline(doc.album)}",
        f"- **Album Artist:** {_escape_inline(doc.album_artist)}",
        f"- **Artist:** {_escape_inline(doc.artist)}",
        f"- **Release Type:** {_escape_inline(doc.release_type)}",
        f"- **Status:** {_escape_inline(doc.status)}",
        f"- **Date:** {_escape_inline(doc.date)}",
        f"- **Original Date:** {_escape_inline(doc.original_date)}",
        f"- **Year:** {_escape_inline(doc.year)}",
        f"- **Genres:** {_format_tag_list(doc.genre)}",
        f"- **Label:** {_escape_inline(doc.label)}",
        f"- **Catalog Number:** {_escape_inline(doc.catalog_number)}",
        f"- **Copyright:** {_escape_inline(doc.copyright)}",
        "",
        "## Distribución",
        f"- **Primary Genre:** {_escape_inline(dist.primary_genre)}",
        f"- **Secondary Genre:** {_escape_inline(dist.secondary_genre)}",
        f"- **Language:** {_escape_inline(dist.language)}",
        f"- **Instrumental:** {_format_bool(dist.instrumental)}",
        f"- **Explicit:** {_format_bool(dist.explicit)}",
        f"- **UPC:** {_escape_inline(dist.upc)}",
        "",
        "## Tracklist",
        "| Disc | Track | Title | Artist | Duration | Format | File | ISRC |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    custom_genre_lines: list[str] = []
    for track in doc.tracks:
        track_col = f"{track.track_number:02d}/{track.total_tracks:02d}"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(track.disc_number),
                    track_col,
                    _escape_cell(track.title),
                    _escape_cell(track.artist),
                    format_duration(track.duration_seconds),
                    _escape_cell(track.format),
                    _escape_cell(track.filename),
                    _escape_cell(track.isrc),
                ]
            )
            + " |"
        )
        if track.genre and track.genre != doc.genre:
            custom_genre_lines.append(
                f"- **{track.disc_number}-{track.track_number:02d} Genres:** {_format_tag_list(track.genre)}"
            )

    if custom_genre_lines:
        lines.append("")
        lines.append("### Géneros por Pista")
        lines.extend(custom_genre_lines)

    lines.extend(["", "## Créditos y Notas"])
    if dist.credits:
        fence = _choose_code_fence(dist.credits)
        lines.append(f"{fence}text")
        lines.append(dist.credits)
        lines.append(fence)
    else:
        lines.append(_NO_CREDITS_MARKER)

    overall_str = (
        "PASS" if doc.all_verified is True else ("FAIL" if doc.all_verified is False else "N/A")
    )
    lines.extend(
        [
            "",
            "## Reporte de Verificación",
            f"- **Estado General:** {overall_str}",
            f"- **Portada:** {_escape_inline(doc.cover_file)}",
            "",
            "| Disc | Track | Estado | Portada | ID3 | Notas |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )

    for track in doc.tracks:
        status_cell = (
            "PASS"
            if track.verified is True
            else ("FAIL" if track.verified is False else _EMPTY_MARKER)
        )
        cover_cell = (
            _format_bool(track.has_cover)
            if track.has_cover is not None
            else _EMPTY_MARKER
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(track.disc_number),
                    f"{track.track_number:02d}",
                    status_cell,
                    cover_cell,
                    _escape_cell(track.id3_version),
                    _escape_cell(track.verification_notes or "OK"),
                ]
            )
            + " |"
        )

    return "\n".join(lines) + "\n"


def _parse_kv_bullets(section_lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in section_lines:
        match = re.match(r"^\s*-\s+\*\*(.+?):\*\*\s*(.*)$", line)
        if match:
            result[match.group(1).strip()] = match.group(2).strip()
    return result


def _optional_kv(kv: dict[str, str], key: str) -> str | None:
    return _unescape_inline(kv.get(key))


def _split_markdown_sections(content: str) -> dict[str, list[str]]:
    """Divide secciones `## ` ignorando cualquier encabezado dentro de bloques fenced (```)."""
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    active_fence: str | None = None

    for line in content.splitlines():
        stripped = line.strip()
        if active_fence is None:
            fence_open = re.match(r"^(`{3,})(?:[a-zA-Z0-9_-]*)?\s*$", stripped)
            if fence_open:
                active_fence = fence_open.group(1)
                if current_section is not None:
                    sections[current_section].append(line)
                continue
            heading_match = re.match(r"^##\s+(.+?)\s*$", line)
            if heading_match:
                current_section = heading_match.group(1).strip()
                sections[current_section] = []
                continue
        else:
            if stripped == active_fence:
                active_fence = None
        if current_section is not None:
            sections[current_section].append(line)

    return sections


def _parse_credits_section(section_lines: list[str]) -> str | None:
    """Extrae créditos desde bloque fenced (preferido) o texto plano heredado."""
    trimmed = list(section_lines)
    while trimmed and not trimmed[0].strip():
        trimmed.pop(0)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    if not trimmed:
        return None

    first = trimmed[0].strip()
    fence_open = re.match(r"^(`{3,})(?:[a-zA-Z0-9_-]*)?\s*$", first)
    if fence_open and len(trimmed) >= 2 and trimmed[-1].strip() == fence_open.group(1):
        inner = "\n".join(trimmed[1:-1])
        return inner if inner.strip() else None

    raw = "\n".join(trimmed).strip()
    if not raw or raw in {_NO_CREDITS_MARKER, _EMPTY_MARKER}:
        return None
    return raw


def parse_release_info_md(content: str) -> ReleaseInfoDocument:
    """Reimporta un archivo `release-info.md` de vuelta a `ReleaseInfoDocument` sin pérdida."""
    sections = _split_markdown_sections(content)

    identity_kv = _parse_kv_bullets(sections.get("Identidad", []))
    dist_kv = _parse_kv_bullets(sections.get("Distribución", []))
    verif_lines = sections.get("Reporte de Verificación", [])
    verif_kv = _parse_kv_bullets(verif_lines)

    album_genres = _parse_tag_list(identity_kv.get("Genres", ""))
    credits = _parse_credits_section(sections.get("Créditos y Notas", []))

    distribution = DistributionInfo(
        primary_genre=_optional_kv(dist_kv, "Primary Genre"),
        secondary_genre=_optional_kv(dist_kv, "Secondary Genre"),
        language=_optional_kv(dist_kv, "Language"),
        instrumental=_parse_bool(_optional_kv(dist_kv, "Instrumental")),
        explicit=_parse_bool(_optional_kv(dist_kv, "Explicit")),
        upc=_optional_kv(dist_kv, "UPC"),
        credits=credits,
    )

    tracklist_lines = sections.get("Tracklist", [])
    per_track_genres: dict[tuple[int, int], list[str]] = {}
    for line in tracklist_lines:
        gmatch = re.match(r"^\s*-\s+\*\*(\d+)-(\d+)\s+Genres:\*\*\s*(.+)$", line)
        if gmatch:
            per_track_genres[(int(gmatch.group(1)), int(gmatch.group(2)))] = _parse_tag_list(
                gmatch.group(3)
            )

    verif_map: dict[tuple[int, int], dict[str, object]] = {}
    verif_table_rows = [l for l in verif_lines if l.strip().startswith("|")]
    for row in verif_table_rows[2:]:
        cells = _split_md_row(row)
        if len(cells) < 6:
            continue
        disc_num = int(cells[0])
        track_num = int(cells[1])
        status_raw = cells[2].strip().upper()
        verified = True if status_raw == "PASS" else (False if status_raw == "FAIL" else None)
        cover_cell = _unescape_cell(cells[3])
        has_cover = _parse_bool(cover_cell) if cover_cell is not None else None
        id3_version = _unescape_cell(cells[4])
        notes_raw = _unescape_cell(cells[5])
        notes = None if notes_raw in (None, "OK") else notes_raw
        verif_map[(disc_num, track_num)] = {
            "verified": verified,
            "has_cover": has_cover,
            "id3_version": id3_version,
            "verification_notes": notes,
        }

    tracks: list[ReleaseInfoTrack] = []
    track_table_rows = [l for l in tracklist_lines if l.strip().startswith("|")]
    for row in track_table_rows[2:]:
        cells = _split_md_row(row)
        if len(cells) < 8:
            continue
        disc_num = int(cells[0])
        track_col = cells[1].strip()
        if "/" in track_col:
            t_str, tot_str = track_col.split("/", 1)
            track_num, total_tracks = int(t_str), int(tot_str)
        else:
            track_num = int(track_col)
            total_tracks = track_num

        title = _unescape_cell(cells[2]) or ""
        artist = _unescape_cell(cells[3]) or (_optional_kv(identity_kv, "Artist") or "")
        duration_seconds = parse_duration(cells[4])
        fmt_raw = _unescape_cell(cells[5])
        filename = _unescape_cell(cells[6])
        isrc = _unescape_cell(cells[7])

        v_info = verif_map.get((disc_num, track_num), {})
        tracks.append(
            ReleaseInfoTrack(
                disc_number=disc_num,
                track_number=track_num,
                total_tracks=total_tracks,
                title=title,
                artist=artist,
                genre=per_track_genres.get((disc_num, track_num), list(album_genres)),
                duration_seconds=duration_seconds,
                format=fmt_raw,  # type: ignore[arg-type]
                filename=filename,
                isrc=isrc,
                verified=v_info.get("verified"),  # type: ignore[arg-type]
                has_cover=v_info.get("has_cover"),  # type: ignore[arg-type]
                id3_version=v_info.get("id3_version"),  # type: ignore[arg-type]
                verification_notes=v_info.get("verification_notes"),  # type: ignore[arg-type]
            )
        )

    overall_raw = (verif_kv.get("Estado General") or "").strip().upper()
    all_verified = True if overall_raw == "PASS" else (False if overall_raw == "FAIL" else None)

    date_val = _optional_kv(identity_kv, "Date") or ""
    album_artist = _optional_kv(identity_kv, "Album Artist") or ""
    return ReleaseInfoDocument(
        album=_optional_kv(identity_kv, "Album") or "",
        album_artist=album_artist,
        artist=_optional_kv(identity_kv, "Artist") or album_artist,
        release_type=_optional_kv(identity_kv, "Release Type") or "",
        status=(_optional_kv(identity_kv, "Status") or "Final"),  # type: ignore[arg-type]
        date=date_val,
        original_date=_optional_kv(identity_kv, "Original Date") or date_val,
        year=_optional_kv(identity_kv, "Year") or date_val[:4],
        genre=album_genres,
        label=_optional_kv(identity_kv, "Label"),
        catalog_number=_optional_kv(identity_kv, "Catalog Number"),
        copyright=_optional_kv(identity_kv, "Copyright"),
        distribution=distribution,
        cover_file=_optional_kv(verif_kv, "Portada"),
        all_verified=all_verified,
        tracks=tracks,
    )


def write_release_info(
    album_dir: Path, doc: ReleaseInfoDocument
) -> tuple[Path, Path]:
    """Escribe atómicamente `release-info.json` y `release-info.md` en `album_dir`."""
    album_dir = Path(album_dir)
    json_path = _write_text_atomic(
        album_dir / "release-info.json", render_release_info_json(doc)
    )
    md_path = _write_text_atomic(
        album_dir / "release-info.md", render_release_info_md(doc)
    )
    return json_path, md_path


def read_release_info(path: Path) -> ReleaseInfoDocument:
    """Lee `release-info.json` o `release-info.md` según su extensión."""
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        return parse_release_info_json(content)
    if suffix == ".md":
        return parse_release_info_md(content)
    raise ValueError(f"Formato de release-info no soportado: {suffix!r}")
