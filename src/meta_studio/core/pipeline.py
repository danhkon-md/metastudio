from __future__ import annotations

import threading
from pathlib import Path

from meta_studio.core.converter import reset_cancellation
from meta_studio.core.cover import process_cover, save_cover_atomic
from meta_studio.core.fs import cleanup_orphan_temps, finalize_temp_file
from meta_studio.core.models import (
    ReleaseInput,
    ReleaseProcessResult,
    TrackInput,
    TrackMetadata,
    TrackProcessResult,
)
from meta_studio.core.sanitizer import build_album_dir, build_track_filename
from meta_studio.core.tagger import TaggingError, resolve_target_format, write_tagged_copy
from meta_studio.core.verifier import (
    VerificationError,
    read_audio_duration,
    verify_audio_file,
)

_PIPELINE_LOCK = threading.Lock()
_ACTIVE_RELEASES = 0
_TRACKED_DIRS: set[Path] = set()


def has_active_releases() -> bool:
    """Indica si existe algún procesamiento de lanzamiento en curso."""
    with _PIPELINE_LOCK:
        return _ACTIVE_RELEASES > 0


def cleanup_tracked_orphan_temps() -> int:
    """Barre archivos temporales huérfanos en las carpetas de salida recientes/activas."""
    with _PIPELINE_LOCK:
        dirs = list(_TRACKED_DIRS)
    return cleanup_orphan_temps(dirs)


def process_release(
    release: ReleaseInput,
    *,
    raise_on_verification_error: bool = True,
    ffmpeg_bin: str = "ffmpeg",
) -> ReleaseProcessResult:
    """Procesa un lanzamiento con Copy-First, prevalidación, verificación previa a publicación y escritura atómica."""
    assert release.year is not None
    reset_cancellation()

    # 1. Prevalidar carpeta, portada en memoria, archivos origen y rutas/duplicados antes de escribir nada
    album_dir = build_album_dir(
        output_dir=release.output_dir,
        album_artist=release.album_artist,
        year=release.year,
        album=release.album,
        status=release.status,
    )

    cover_bytes: bytes | None = None
    if release.cover_path is not None:
        cover_bytes = process_cover(release.cover_path)

    planned: list[tuple[TrackInput, TrackMetadata, float, Path]] = []
    seen_dests: set[str] = set()
    for track_input, metadata in release.resolve_tracks():
        if not track_input.source_path.is_file():
            raise TaggingError(
                f"Pista {metadata.track_number} ({metadata.title!r}): archivo de origen no encontrado: {track_input.source_path}"
            )
        target_format, _will_convert = resolve_target_format(
            track_input.source_path, release.wav_conversion
        )
        try:
            filename = build_track_filename(
                track_number=metadata.track_number,
                title=metadata.title,
                extension=target_format,
                disc_number=metadata.disc_number,
                total_discs=metadata.total_discs,
                parent_dir=album_dir,
            )
        except ValueError as exc:
            raise ValueError(
                f"Pista {metadata.track_number} (disco {metadata.disc_number}): título inválido tras sanitizar ({metadata.title!r})"
            ) from exc

        dedupe_key = filename.casefold()
        if dedupe_key in seen_dests:
            raise ValueError(
                f"Nombre de archivo destino duplicado tras sanitizar: {filename!r}"
            )
        seen_dests.add(dedupe_key)

        source_duration = read_audio_duration(track_input.source_path)
        dest_path = album_dir / filename
        planned.append((track_input, metadata, source_duration, dest_path))

    # 2. Crear carpeta y procesar pista por pista verificando ANTES de publicar
    global _ACTIVE_RELEASES
    with _PIPELINE_LOCK:
        _ACTIVE_RELEASES += 1
        _TRACKED_DIRS.add(album_dir)

    try:
        album_dir.mkdir(parents=True, exist_ok=True)

        saved_cover_path: Path | None = None
        if cover_bytes is not None:
            saved_cover_path = save_cover_atomic(cover_bytes, album_dir / "cover.jpg")

        track_results: list[TrackProcessResult] = []
        for track_input, metadata, source_duration, dest_path in planned:
            staged_path, actual_format, converted = write_tagged_copy(
                source_path=track_input.source_path,
                dest_path=dest_path,
                metadata=metadata,
                cover_bytes=cover_bytes,
                wav_conversion=release.wav_conversion,
                ffmpeg_bin=ffmpeg_bin,
                publish=False,
            )

            try:
                verification = verify_audio_file(
                    staged_path,
                    metadata,
                    expected_cover_bytes=cover_bytes,
                    expected_duration_seconds=source_duration,
                )
                if verification.passed:
                    finalize_temp_file(staged_path, dest_path)
                    verification = verification.model_copy(
                        update={"file_path": dest_path}
                    )
                else:
                    staged_path.unlink(missing_ok=True)
                    verification = verification.model_copy(
                        update={"file_path": dest_path}
                    )
                    if raise_on_verification_error:
                        raise VerificationError(verification)
            except Exception:
                staged_path.unlink(missing_ok=True)
                raise

            track_results.append(
                TrackProcessResult(
                    source_path=track_input.source_path,
                    output_path=dest_path,
                    format=actual_format,
                    converted=converted,
                    isrc=track_input.isrc,
                    metadata=metadata,
                    verification=verification,
                )
            )

        all_verified = all(item.verification.passed for item in track_results)
        from meta_studio.services.release_info import (
            build_release_info,
            write_release_info,
        )

        release_info_doc = build_release_info(
            release, track_results, cover_path=saved_cover_path
        )
        json_path, md_path = write_release_info(album_dir, release_info_doc)

        return ReleaseProcessResult(
            album_dir=album_dir,
            cover_path=saved_cover_path,
            release_info_json_path=json_path,
            release_info_md_path=md_path,
            tracks=track_results,
            all_verified=all_verified,
        )
    finally:
        with _PIPELINE_LOCK:
            _ACTIVE_RELEASES = max(0, _ACTIVE_RELEASES - 1)
