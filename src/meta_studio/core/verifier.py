from __future__ import annotations

from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

from meta_studio.core.models import (
    TrackMetadata,
    VerificationMismatch,
    VerificationResult,
)
from meta_studio.core.tagger import detect_audio_format

LOSSLESS_DURATION_TOLERANCE = 0.05
LOSSY_DURATION_TOLERANCE = 0.25


class VerificationError(RuntimeError):
    """Error lanzado cuando la Read-Back Verification detecta discrepancias."""

    def __init__(self, result: VerificationResult) -> None:
        self.result = result
        summary = ", ".join(
            f"{m.field} (esperado={m.expected!r}, leído={m.actual!r})"
            for m in result.mismatches
        )
        super().__init__(
            f"Read-Back Verification falló para {result.file_path.name}: {summary}"
        )


def read_audio_duration(path: Path) -> float:
    """Lee la duración en segundos de un archivo FLAC, MP3 o WAV con mutagen."""
    fmt = detect_audio_format(path)
    if fmt == "flac":
        return float(FLAC(path).info.length)
    if fmt == "mp3":
        return float(MP3(path).info.length)
    if fmt == "wav":
        return float(WAVE(path).info.length)
    raise ValueError(f"Formato no soportado: {fmt}")


def _has_id3v1_trailer(path: Path) -> bool:
    """Comprueba si los últimos 128 bytes comienzan con la firma ID3v1 `b'TAG'`."""
    size = path.stat().st_size
    if size < 128:
        return False
    with path.open("rb") as fh:
        fh.seek(size - 128)
        return fh.read(3) == b"TAG"


def _has_leading_id3v2(path: Path) -> bool:
    """Comprueba si el archivo comienza con una cabecera ID3v2 (`b'ID3'`)."""
    with path.open("rb") as fh:
        return fh.read(3) == b"ID3"


def _get_first_text(tags: ID3, frame_id: str) -> str | None:
    frames = tags.getall(frame_id)
    if not frames:
        return None
    text = getattr(frames[0], "text", None)
    if not text:
        return None
    return str(text[0])


def _get_txxx_text(tags: ID3, desc: str) -> str | None:
    for frame in tags.getall("TXXX"):
        if frame.desc == desc and frame.text:
            return str(frame.text[0])
    return None


def _parse_slash_pair(raw: str | None) -> tuple[str | None, str | None]:
    if raw is None:
        return None, None
    if "/" in raw:
        left, right = raw.split("/", 1)
        return left, right
    return raw, None


def _read_flac_state(
    path: Path,
) -> tuple[float, dict[str, object], bytes | None, str | None, int, tuple[int, int, int] | None]:
    audio = FLAC(path)
    duration = float(audio.info.length)

    def _single(key: str) -> str | None:
        vals = audio.get(key)
        if not vals:
            return None
        return str(vals[0])

    read_tags: dict[str, object] = {
        "TITLE": _single("TITLE"),
        "ARTIST": _single("ARTIST"),
        "ALBUM": _single("ALBUM"),
        "ALBUMARTIST": _single("ALBUMARTIST"),
        "DATE": _single("DATE"),
        "ORIGINALDATE": _single("ORIGINALDATE"),
        "YEAR": _single("YEAR"),
        "TRACKNUMBER": _single("TRACKNUMBER"),
        "TOTALTRACKS": _single("TOTALTRACKS"),
        "DISCNUMBER": _single("DISCNUMBER"),
        "TOTALDISCS": _single("TOTALDISCS"),
        "GENRE": [str(g) for g in audio.get("GENRE", [])],
        "RELEASETYPE": _single("RELEASETYPE"),
        "LABEL": _single("LABEL"),
        "CATALOGNUMBER": _single("CATALOGNUMBER"),
        "COPYRIGHT": _single("COPYRIGHT"),
    }

    pictures = list(audio.pictures)
    cover_data: bytes | None = None
    cover_mime: str | None = None
    if pictures:
        cover_data = bytes(pictures[0].data)
        cover_mime = str(pictures[0].mime)

    return duration, read_tags, cover_data, cover_mime, len(pictures), None


def _read_id3_state(
    path: Path, fmt: str
) -> tuple[float, dict[str, object], bytes | None, str | None, int, tuple[int, int, int] | None]:
    audio = MP3(path) if fmt == "mp3" else WAVE(path)
    duration = float(audio.info.length)
    tags = audio.tags

    if tags is None:
        return duration, {}, None, None, 0, None

    id3_version = tuple(int(v) for v in tags.version)
    track_num, total_tracks = _parse_slash_pair(_get_first_text(tags, "TRCK"))
    disc_num, total_discs = _parse_slash_pair(_get_first_text(tags, "TPOS"))

    tcon_frames = tags.getall("TCON")
    genres: list[str] = (
        [str(g) for g in tcon_frames[0].text] if tcon_frames and tcon_frames[0].text else []
    )

    read_tags: dict[str, object] = {
        "TITLE": _get_first_text(tags, "TIT2"),
        "ARTIST": _get_first_text(tags, "TPE1"),
        "ALBUM": _get_first_text(tags, "TALB"),
        "ALBUMARTIST": _get_first_text(tags, "TPE2"),
        "DATE": _get_first_text(tags, "TDRC"),
        "ORIGINALDATE": _get_first_text(tags, "TDOR"),
        "YEAR": _get_txxx_text(tags, "YEAR"),
        "TRACKNUMBER": track_num,
        "TOTALTRACKS": total_tracks,
        "DISCNUMBER": disc_num,
        "TOTALDISCS": total_discs,
        "GENRE": genres,
        "RELEASETYPE": _get_txxx_text(tags, "MusicBrainz Album Type"),
        "LABEL": _get_first_text(tags, "TPUB"),
        "CATALOGNUMBER": _get_txxx_text(tags, "CATALOGNUMBER"),
        "COPYRIGHT": _get_first_text(tags, "TCOP"),
    }

    apics = tags.getall("APIC")
    cover_data: bytes | None = None
    cover_mime: str | None = None
    if apics:
        cover_data = bytes(apics[0].data)
        cover_mime = str(apics[0].mime)

    return duration, read_tags, cover_data, cover_mime, len(apics), id3_version


def verify_audio_file(
    file_path: Path,
    expected_metadata: TrackMetadata,
    *,
    expected_cover_bytes: bytes | None = None,
    expected_duration_seconds: float | None = None,
) -> VerificationResult:
    """Reabre desde cero el archivo con mutagen y verifica duración, higiene binaria, portada y tags."""
    file_path = Path(file_path)
    fmt = detect_audio_format(file_path)

    if fmt == "flac":
        duration, read_tags, cover_data, cover_mime, cover_count, id3_version = (
            _read_flac_state(file_path)
        )
    else:
        duration, read_tags, cover_data, cover_mime, cover_count, id3_version = (
            _read_id3_state(file_path, fmt)
        )

    mismatches: list[VerificationMismatch] = []

    # 1. Duración
    if duration <= 0.0:
        mismatches.append(
            VerificationMismatch(field="DURATION", expected="> 0", actual=duration)
        )
    elif expected_duration_seconds is not None:
        tolerance = (
            LOSSY_DURATION_TOLERANCE if fmt == "mp3" else LOSSLESS_DURATION_TOLERANCE
        )
        if abs(duration - expected_duration_seconds) > tolerance:
            mismatches.append(
                VerificationMismatch(
                    field="DURATION",
                    expected=expected_duration_seconds,
                    actual=duration,
                )
            )

    # 2. Higiene de contenedor e ID3v2.4
    if fmt == "flac":
        if _has_leading_id3v2(file_path):
            mismatches.append(
                VerificationMismatch(
                    field="FLAC_ID3_HEADER",
                    expected="ausente",
                    actual="cabecera ID3v2 al inicio de FLAC",
                )
            )
    else:
        if id3_version != (2, 4, 0):
            mismatches.append(
                VerificationMismatch(
                    field="ID3_VERSION", expected=(2, 4, 0), actual=id3_version
                )
            )
        if _has_id3v1_trailer(file_path):
            mismatches.append(
                VerificationMismatch(
                    field="ID3V1_TRAILER",
                    expected="ausente",
                    actual="bloque ID3v1 detectado en los últimos 128 bytes",
                )
            )

    # 3. Portada incrustada
    has_cover = cover_count > 0
    if expected_cover_bytes is not None:
        cover_matches = (
            cover_count == 1
            and cover_mime == "image/jpeg"
            and cover_data == expected_cover_bytes
        )
        if not cover_matches:
            mismatches.append(
                VerificationMismatch(
                    field="COVER",
                    expected=f"1x image/jpeg ({len(expected_cover_bytes)} bytes)",
                    actual=f"{cover_count}x {cover_mime} ({len(cover_data) if cover_data else 0} bytes)",
                )
            )
    else:
        cover_matches = cover_count == 0
        if not cover_matches:
            mismatches.append(
                VerificationMismatch(
                    field="COVER",
                    expected="sin portada incrustada",
                    actual=f"{cover_count} portada(s) incrustada(s)",
                )
            )

    # 4. Tags exactos (obligatorios, derivados, TOTALDISCS y opcionales si están presentes)
    expected_tags = expected_metadata.expected_tag_map()
    for field_name, expected_val in expected_tags.items():
        actual_val = read_tags.get(field_name)
        if actual_val != expected_val:
            mismatches.append(
                VerificationMismatch(
                    field=field_name, expected=expected_val, actual=actual_val
                )
            )

    return VerificationResult(
        file_path=file_path,
        passed=len(mismatches) == 0,
        duration_seconds=duration,
        expected_duration_seconds=expected_duration_seconds,
        has_cover=has_cover,
        cover_matches=cover_matches,
        id3_version=id3_version,
        mismatches=mismatches,
        read_tags=read_tags,
    )
