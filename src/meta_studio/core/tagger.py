from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TCOP,
    TDOR,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TPUB,
    TRCK,
    TXXX,
    Encoding,
)
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

from meta_studio.core.converter import convert_wav
from meta_studio.core.cover import get_jpeg_dimensions
from meta_studio.core.fs import finalize_temp_file, fsync_file
from meta_studio.core.models import SupportedFormat, TrackMetadata, WavConversionMode

SUPPORTED_EXTENSIONS: dict[str, SupportedFormat] = {
    ".flac": "flac",
    ".mp3": "mp3",
    ".wav": "wav",
}


class TaggingError(RuntimeError):
    """Error al copiar, limpiar o escribir metadata en un archivo de audio."""


def detect_audio_format(path: Path) -> SupportedFormat:
    """Detecta el formato soportado a partir de la extensión del archivo."""
    ext = Path(path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise TaggingError(f"Formato de audio no soportado: {ext!r}")
    return SUPPORTED_EXTENSIONS[ext]


def resolve_target_format(
    source_path: Path, wav_conversion: WavConversionMode = "keep"
) -> tuple[SupportedFormat, bool]:
    """Determina el formato final de salida y si requiere conversión con ffmpeg."""
    source_format = detect_audio_format(source_path)
    if source_format == "wav" and wav_conversion == "flac":
        return "flac", True
    if source_format == "wav" and wav_conversion == "mp3_320":
        return "mp3", True
    return source_format, False


def _populate_id3_v24_frames(
    tags: ID3,
    metadata: TrackMetadata,
    cover_bytes: bytes | None,
) -> None:
    """Carga todos los frames ID3v2.4 (UTF-8) en un contenedor ID3 limpio."""
    assert metadata.original_date is not None
    assert metadata.year is not None

    utf8 = Encoding.UTF8
    tags.add(TIT2(encoding=utf8, text=[metadata.title]))
    tags.add(TPE1(encoding=utf8, text=[metadata.artist]))
    tags.add(TALB(encoding=utf8, text=[metadata.album]))
    tags.add(TPE2(encoding=utf8, text=[metadata.album_artist]))
    tags.add(TDRC(encoding=utf8, text=[metadata.date]))
    tags.add(TDOR(encoding=utf8, text=[metadata.original_date]))
    tags.add(TXXX(encoding=utf8, desc="YEAR", text=[metadata.year]))
    tags.add(TRCK(encoding=utf8, text=[f"{metadata.track_number}/{metadata.total_tracks}"]))
    tags.add(TPOS(encoding=utf8, text=[f"{metadata.disc_number}/{metadata.total_discs}"]))

    # Multi-valor real en ID3v2.4 (nunca concatenado con comas)
    tags.add(TCON(encoding=utf8, text=list(metadata.genre)))
    tags.add(
        TXXX(encoding=utf8, desc="MusicBrainz Album Type", text=[metadata.release_type])
    )

    if metadata.label is not None:
        tags.add(TPUB(encoding=utf8, text=[metadata.label]))
    if metadata.catalog_number is not None:
        tags.add(
            TXXX(encoding=utf8, desc="CATALOGNUMBER", text=[metadata.catalog_number])
        )
    if metadata.copyright is not None:
        tags.add(TCOP(encoding=utf8, text=[metadata.copyright]))

    if cover_bytes is not None:
        tags.add(
            APIC(
                encoding=utf8,
                mime="image/jpeg",
                type=3,
                desc="Front Cover",
                data=cover_bytes,
            )
        )


def _tag_flac_inplace(
    temp_path: Path, metadata: TrackMetadata, cover_bytes: bytes | None
) -> None:
    """Limpia tags, portadas e ID3 inicial de la copia temporal FLAC y escribe Vorbis Comments."""
    assert metadata.original_date is not None
    assert metadata.year is not None

    audio = FLAC(temp_path)
    # Limpieza completa de cualquier metadata o portada previa
    audio.clear()
    audio.clear_pictures()

    audio["TITLE"] = [metadata.title]
    audio["ARTIST"] = [metadata.artist]
    audio["ALBUM"] = [metadata.album]
    audio["ALBUMARTIST"] = [metadata.album_artist]
    audio["DATE"] = [metadata.date]
    audio["ORIGINALDATE"] = [metadata.original_date]
    audio["YEAR"] = [metadata.year]
    audio["TRACKNUMBER"] = [str(metadata.track_number)]
    audio["TOTALTRACKS"] = [str(metadata.total_tracks)]
    audio["DISCNUMBER"] = [str(metadata.disc_number)]
    audio["TOTALDISCS"] = [str(metadata.total_discs)]
    # Múltiples etiquetas GENRE reales en Vorbis Comments
    audio["GENRE"] = list(metadata.genre)
    audio["RELEASETYPE"] = [metadata.release_type]

    if metadata.label is not None:
        audio["LABEL"] = [metadata.label]
    if metadata.catalog_number is not None:
        audio["CATALOGNUMBER"] = [metadata.catalog_number]
    if metadata.copyright is not None:
        audio["COPYRIGHT"] = [metadata.copyright]

    if cover_bytes is not None:
        width, height = get_jpeg_dimensions(cover_bytes)
        picture = Picture()
        picture.type = 3
        picture.mime = "image/jpeg"
        picture.desc = "Front Cover"
        picture.width = width
        picture.height = height
        picture.depth = 24
        picture.data = cover_bytes
        audio.add_picture(picture)

    # deleteid3=True elimina cualquier cabecera ID3v2 prepended en FLAC
    audio.save(deleteid3=True)


def _tag_mp3_inplace(
    temp_path: Path, metadata: TrackMetadata, cover_bytes: bytes | None
) -> None:
    """Limpia cualquier ID3v1/ID3v2/APIC previo en la copia temporal MP3 y guarda ID3v2.4."""
    audio = MP3(temp_path)
    audio.delete()

    tags = ID3()
    _populate_id3_v24_frames(tags, metadata, cover_bytes)
    # Forzar explícitamente v2_version=4 y desactivar ID3v1
    tags.save(temp_path, v1=0, v2_version=4)


def _strip_trailing_id3v1(path: Path) -> None:
    """Elimina cualquier bloque ID3v1 (128 bytes finales con 'TAG') si existía en el archivo."""
    size = path.stat().st_size
    if size < 128:
        return
    with path.open("rb+") as fh:
        fh.seek(size - 128)
        if fh.read(3) == b"TAG":
            fh.truncate(size - 128)


def _tag_wav_inplace(
    temp_path: Path, metadata: TrackMetadata, cover_bytes: bytes | None
) -> None:
    """Limpia cualquier ID3v1/ID3v2/APIC previo en la copia temporal WAV y guarda ID3v2.4 sin ID3v1."""
    audio = WAVE(temp_path)
    if audio.tags is not None:
        audio.delete()
    _strip_trailing_id3v1(temp_path)

    audio = WAVE(temp_path)
    audio.add_tags()
    assert audio.tags is not None
    _populate_id3_v24_frames(audio.tags, metadata, cover_bytes)
    # Forzar explícitamente v1=0 y v2_version=4
    audio.save(v1=0, v2_version=4)


def write_tagged_copy(
    source_path: Path,
    dest_path: Path,
    metadata: TrackMetadata,
    *,
    cover_bytes: bytes | None = None,
    wav_conversion: WavConversionMode = "keep",
    ffmpeg_bin: str = "ffmpeg",
    publish: bool = True,
) -> tuple[Path, SupportedFormat, bool]:
    """Genera una copia taggeada con Copy-First y escritura atómica.

    1. Nunca modifica, renombra ni borra `source_path`.
    2. Trabaja en un archivo temporal en el directorio destino (permisos `0o600` mientras edita).
    3. Limpia tags y portadas previas antes de escribir los nuevos.
    4. Si `publish=True`, hace `fsync`, ajusta permisos a `0o644` y mueve atómicamente (`os.replace`).
       Si `publish=False`, devuelve el archivo temporal `staged` listo para verificación previa.
    """
    source_path = Path(source_path)
    dest_path = Path(dest_path)

    if not source_path.is_file():
        raise TaggingError(f"Archivo de origen no encontrado: {source_path}")
    if source_path.resolve() == dest_path.resolve():
        raise TaggingError(
            "Violación de Copy-First: la ruta de destino no puede ser el archivo de origen"
        )

    target_format, converted = resolve_target_format(source_path, wav_conversion)
    expected_suffix = f".{target_format}"
    if dest_path.suffix.lower() != expected_suffix:
        raise TaggingError(
            f"La extensión destino ({dest_path.suffix}) no coincide con el formato {target_format}"
        )

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=dest_path.parent, prefix=".tmp_meta_", suffix=expected_suffix
    )
    os.close(fd)
    temp_path = Path(tmp_name)

    try:
        if converted:
            assert wav_conversion in ("flac", "mp3_320")
            convert_wav(
                source_path, wav_conversion, temp_path, ffmpeg_bin=ffmpeg_bin
            )
        else:
            shutil.copy2(source_path, temp_path)

        # Garantizar permisos escribibles incluso si el origen era solo lectura (0444)
        os.chmod(temp_path, 0o600)

        if target_format == "flac":
            _tag_flac_inplace(temp_path, metadata, cover_bytes)
        elif target_format == "mp3":
            _tag_mp3_inplace(temp_path, metadata, cover_bytes)
        elif target_format == "wav":
            _tag_wav_inplace(temp_path, metadata, cover_bytes)
        else:
            raise TaggingError(f"Formato no soportado: {target_format}")

        if publish:
            finalize_temp_file(temp_path, dest_path)
            return dest_path, target_format, converted

        fsync_file(temp_path)
        return temp_path, target_format, converted
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
