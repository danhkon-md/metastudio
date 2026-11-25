from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReleaseStatus = Literal["Final", "Demo", "WIP"]
WavConversionMode = Literal["keep", "flac", "mp3_320"]
SupportedFormat = Literal["flac", "mp3", "wav"]

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_RE = re.compile(r"^\d{4}$")


def _validate_iso_date(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not _ISO_DATE_RE.match(cleaned):
        raise ValueError(f"{field_name} debe tener formato YYYY-MM-DD, recibido: {value!r}")
    try:
        datetime.date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"{field_name} no es una fecha válida: {value!r}") from exc
    return cleaned


def _validate_required_str(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} no puede estar vacío")
    return cleaned


def _normalize_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def _validate_genre_list(value: list[str]) -> list[str]:
    if not isinstance(value, list) or len(value) == 0:
        raise ValueError("GENRE debe ser una lista no vacía de géneros")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Cada género debe ser un string")
        entry = item.strip()
        if not entry:
            raise ValueError("GENRE no puede contener elementos vacíos")
        if entry not in seen:
            cleaned.append(entry)
            seen.add(entry)
    if not cleaned:
        raise ValueError("GENRE debe contener al menos un género válido")
    return cleaned


class TrackMetadata(BaseModel):
    """Metadata resuelta y validada para una pista individual."""

    model_config = ConfigDict(extra="forbid")

    title: str
    artist: str
    album: str
    album_artist: str
    date: str
    original_date: str | None = None
    year: str | None = None
    track_number: int = Field(ge=1)
    total_tracks: int = Field(ge=1)
    disc_number: int = Field(default=1, ge=1)
    total_discs: int = Field(default=1, ge=1)
    genre: list[str]
    release_type: str
    label: str | None = None
    catalog_number: str | None = None
    copyright: str | None = None

    @field_validator("title", "artist", "album", "album_artist", "release_type")
    @classmethod
    def _check_required_text(cls, value: str, info) -> str:
        return _validate_required_str(value, info.field_name.upper())

    @field_validator("label", "catalog_number", "copyright", mode="before")
    @classmethod
    def _clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Los campos opcionales de texto deben ser strings")
        return _normalize_optional_str(value)

    @field_validator("date")
    @classmethod
    def _check_date(cls, value: str) -> str:
        return _validate_iso_date(value, "DATE")

    @field_validator("original_date", mode="before")
    @classmethod
    def _check_original_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("ORIGINALDATE debe ser un string en formato YYYY-MM-DD")
        cleaned = value.strip()
        if not cleaned:
            return None
        return _validate_iso_date(cleaned, "ORIGINALDATE")

    @field_validator("year", mode="before")
    @classmethod
    def _check_year_format(cls, value: str | int | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            return None
        if not _YEAR_RE.match(cleaned):
            raise ValueError(f"YEAR debe tener formato YYYY, recibido: {value!r}")
        return cleaned

    @field_validator("genre")
    @classmethod
    def _check_genre(cls, value: list[str]) -> list[str]:
        return _validate_genre_list(value)

    @model_validator(mode="after")
    def _derive_and_validate_invariants(self) -> TrackMetadata:
        derived_year = self.date[:4]
        if self.year is None:
            self.year = derived_year
        elif self.year != derived_year:
            raise ValueError(
                f"YEAR ({self.year}) debe coincidir con el año de DATE ({derived_year})"
            )

        if self.original_date is None:
            self.original_date = self.date

        if self.track_number > self.total_tracks:
            raise ValueError(
                f"TRACKNUMBER ({self.track_number}) no puede ser mayor que TOTALTRACKS ({self.total_tracks})"
            )
        if self.disc_number > self.total_discs:
            raise ValueError(
                f"DISCNUMBER ({self.disc_number}) no puede ser mayor que total_discs ({self.total_discs})"
            )
        return self

    def expected_tag_map(self) -> dict[str, str | list[str] | None]:
        """Devuelve el mapa canónico de tags esperados para escritura y verificación."""
        assert self.original_date is not None
        assert self.year is not None
        return {
            "TITLE": self.title,
            "ARTIST": self.artist,
            "ALBUM": self.album,
            "ALBUMARTIST": self.album_artist,
            "DATE": self.date,
            "ORIGINALDATE": self.original_date,
            "YEAR": self.year,
            "TRACKNUMBER": str(self.track_number),
            "TOTALTRACKS": str(self.total_tracks),
            "DISCNUMBER": str(self.disc_number),
            "TOTALDISCS": str(self.total_discs),
            "GENRE": list(self.genre),
            "RELEASETYPE": self.release_type,
            "LABEL": self.label,
            "CATALOGNUMBER": self.catalog_number,
            "COPYRIGHT": self.copyright,
        }


class DistributionInfo(BaseModel):
    """Campos opcionales de distribución y créditos para Bandcamp/DistroKid."""

    model_config = ConfigDict(extra="forbid")

    primary_genre: str | None = None
    secondary_genre: str | None = None
    language: str | None = None
    instrumental: bool = False
    explicit: bool = False
    upc: str | None = None
    credits: str | None = None

    @field_validator("primary_genre", "secondary_genre", "language", "upc", mode="before")
    @classmethod
    def _clean_optional_single_line(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Los campos opcionales de distribución deben ser strings")
        return _normalize_optional_str(value)

    @field_validator("credits", mode="before")
    @classmethod
    def _clean_credits(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("credits debe ser un string")
        cleaned = value.strip()
        return cleaned if cleaned else None


class TrackInput(BaseModel):
    """Entrada por pista antes de resolver totales del lanzamiento."""

    model_config = ConfigDict(extra="forbid")

    source_path: Path
    title: str
    artist: str | None = None
    track_number: int = Field(ge=1)
    disc_number: int = Field(default=1, ge=1)
    genre: list[str] | None = None
    isrc: str | None = None

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str) -> str:
        return _validate_required_str(value, "TITLE")

    @field_validator("artist", "isrc", mode="before")
    @classmethod
    def _clean_optional_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Los campos opcionales de texto deben ser strings")
        return _normalize_optional_str(value)

    @field_validator("genre")
    @classmethod
    def _check_optional_genre(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _validate_genre_list(value)


class ReleaseInput(BaseModel):
    """Configuración completa de un lanzamiento a procesar en Copy-First."""

    model_config = ConfigDict(extra="forbid")

    album: str
    album_artist: str
    artist: str | None = None
    date: str
    original_date: str | None = None
    year: str | None = None
    genre: list[str]
    release_type: str
    status: ReleaseStatus = "Final"
    label: str | None = None
    catalog_number: str | None = None
    copyright: str | None = None
    distribution: DistributionInfo = Field(default_factory=DistributionInfo)
    output_dir: Path
    cover_path: Path | None = None
    wav_conversion: WavConversionMode = "keep"
    tracks: list[TrackInput] = Field(min_length=1)

    @field_validator("album", "album_artist", "release_type")
    @classmethod
    def _check_required_text(cls, value: str, info) -> str:
        return _validate_required_str(value, info.field_name.upper())

    @field_validator("artist", "label", "catalog_number", "copyright", mode="before")
    @classmethod
    def _clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Los campos opcionales de texto deben ser strings")
        return _normalize_optional_str(value)

    @field_validator("date")
    @classmethod
    def _check_date(cls, value: str) -> str:
        return _validate_iso_date(value, "DATE")

    @field_validator("original_date", mode="before")
    @classmethod
    def _check_original_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("ORIGINALDATE debe ser un string en formato YYYY-MM-DD")
        cleaned = value.strip()
        if not cleaned:
            return None
        return _validate_iso_date(cleaned, "ORIGINALDATE")

    @field_validator("year", mode="before")
    @classmethod
    def _check_year_format(cls, value: str | int | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            return None
        if not _YEAR_RE.match(cleaned):
            raise ValueError(f"YEAR debe tener formato YYYY, recibido: {value!r}")
        return cleaned

    @field_validator("genre")
    @classmethod
    def _check_genre(cls, value: list[str]) -> list[str]:
        return _validate_genre_list(value)

    @model_validator(mode="after")
    def _derive_and_validate_release(self) -> ReleaseInput:
        derived_year = self.date[:4]
        if self.year is None:
            self.year = derived_year
        elif self.year != derived_year:
            raise ValueError(
                f"YEAR ({self.year}) debe coincidir con el año de DATE ({derived_year})"
            )

        if self.original_date is None:
            self.original_date = self.date

        if self.artist is None:
            self.artist = self.album_artist

        seen_positions: set[tuple[int, int]] = set()
        for track in self.tracks:
            pos = (track.disc_number, track.track_number)
            if pos in seen_positions:
                raise ValueError(
                    f"Posición duplicada en el lanzamiento: disco {track.disc_number}, pista {track.track_number}"
                )
            seen_positions.add(pos)

        return self

    def resolve_tracks(self) -> list[tuple[TrackInput, TrackMetadata]]:
        """Resuelve la metadata completa por pista, calculando totales por disco."""
        total_discs = max(track.disc_number for track in self.tracks)
        per_disc_counts: dict[int, int] = {}
        per_disc_max: dict[int, int] = {}
        for track in self.tracks:
            per_disc_counts[track.disc_number] = per_disc_counts.get(track.disc_number, 0) + 1
            per_disc_max[track.disc_number] = max(
                per_disc_max.get(track.disc_number, 0), track.track_number
            )

        resolved: list[tuple[TrackInput, TrackMetadata]] = []
        for track in self.tracks:
            total_tracks = max(
                per_disc_counts[track.disc_number], per_disc_max[track.disc_number]
            )
            metadata = TrackMetadata(
                title=track.title,
                artist=track.artist or self.artist or self.album_artist,
                album=self.album,
                album_artist=self.album_artist,
                date=self.date,
                original_date=self.original_date,
                year=self.year,
                track_number=track.track_number,
                total_tracks=total_tracks,
                disc_number=track.disc_number,
                total_discs=total_discs,
                genre=track.genre if track.genre is not None else list(self.genre),
                release_type=self.release_type,
                label=self.label,
                catalog_number=self.catalog_number,
                copyright=self.copyright,
            )
            resolved.append((track, metadata))
        return resolved


class VerificationMismatch(BaseModel):
    """Discrepancia individual detectada en Read-Back Verification."""

    field: str
    expected: object
    actual: object


class VerificationResult(BaseModel):
    """Resultado de Read-Back Verification para un archivo de audio procesado."""

    file_path: Path
    passed: bool
    duration_seconds: float
    expected_duration_seconds: float | None = None
    has_cover: bool
    cover_matches: bool
    id3_version: tuple[int, int, int] | None = None
    mismatches: list[VerificationMismatch] = Field(default_factory=list)
    read_tags: dict[str, object] = Field(default_factory=dict)


class TrackProcessResult(BaseModel):
    """Resultado completo de procesar y verificar una pista."""

    source_path: Path
    output_path: Path
    format: SupportedFormat
    converted: bool
    isrc: str | None = None
    metadata: TrackMetadata
    verification: VerificationResult


class ReleaseProcessResult(BaseModel):
    """Resultado completo de procesar un lanzamiento."""

    album_dir: Path
    cover_path: Path | None
    release_info_json_path: Path | None = None
    release_info_md_path: Path | None = None
    tracks: list[TrackProcessResult]
    all_verified: bool
