from __future__ import annotations

import datetime
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from meta_studio.core.fs import finalize_temp_file
from meta_studio.runtime import ensure_user_presets_dir

_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def default_presets_dir() -> Path:
    """Resuelve el directorio escribible de presets (repo `presets/` en dev,
    `~/Library/Application Support/Meta Studio/` en binario compilado)."""
    return ensure_user_presets_dir()


def _dedupe_clean_list(values: list[str], field_name: str) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field_name} debe ser una lista")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise ValueError(f"Cada elemento de {field_name} debe ser un string")
        entry = item.strip()
        if not entry:
            raise ValueError(f"{field_name} no puede contener valores vacíos")
        if entry not in seen:
            cleaned.append(entry)
            seen.add(entry)
    return cleaned


def _write_json_atomic(path: Path, payload: dict[str, object]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".tmp_{path.stem}_", suffix=".json"
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


def _backup_corrupt_json(path: Path) -> Path:
    """Respalda un archivo JSON corrupto como `<name>.corrupt-<timestamp>`."""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.corrupt-{ts}")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.corrupt-{ts}-{counter}")
        counter += 1
    os.replace(path, backup)
    return backup


class ProfilePreset(BaseModel):
    """Perfil reutilizable de lanzamiento (artista, sello, copyright y tipo)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    artist: str
    album_artist: str
    label: str = ""
    copyright: str = ""
    release_type: str = "album"
    catalog_prefix: str = ""

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned or not _PROFILE_ID_RE.match(cleaned):
            raise ValueError(
                f"id de perfil inválido ({value!r}): usar minúsculas, números, '-' o '_'"
            )
        return cleaned

    @field_validator("name", "artist", "album_artist", "release_type")
    @classmethod
    def _validate_non_empty(cls, value: str, info) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} no puede estar vacío")
        return cleaned

    @field_validator("label", "copyright", "catalog_prefix")
    @classmethod
    def _strip_optional(cls, value: str) -> str:
        return value.strip()

    def render_copyright(self, year: str | int) -> str:
        """Expande `{year}` en la plantilla de copyright si está presente."""
        if not self.copyright:
            return ""
        return self.copyright.replace("{year}", str(year))


class ProfilesFile(BaseModel):
    """Contenedor validado para `presets/profiles.json`."""

    model_config = ConfigDict(extra="forbid")

    profiles: list[ProfilePreset] = Field(default_factory=list)

    @field_validator("profiles")
    @classmethod
    def _validate_unique_ids(cls, profiles: list[ProfilePreset]) -> list[ProfilePreset]:
        seen: set[str] = set()
        for profile in profiles:
            if profile.id in seen:
                raise ValueError(f"ID de perfil duplicado: {profile.id!r}")
            seen.add(profile.id)
        return profiles


class GenresCatalog(BaseModel):
    """Catálogo validado de géneros para distribuidora y tags multi-valor."""

    model_config = ConfigDict(extra="forbid")

    distributor_primary: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("distributor_primary")
    @classmethod
    def _validate_distributor_primary(cls, value: list[str]) -> list[str]:
        return _dedupe_clean_list(value, "distributor_primary")

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str]) -> list[str]:
        return _dedupe_clean_list(value, "tags")


class AppSettings(BaseModel):
    """Preferencias locales persistentes de la app (carpeta de salida por defecto)."""

    model_config = ConfigDict(extra="forbid")

    output_dir: str = ""

    @field_validator("output_dir")
    @classmethod
    def _clean_output_dir(cls, value: str) -> str:
        return value.strip()


def _resolve_music_dir() -> Path:
    """Resuelve la carpeta de Música del usuario respetando el sistema operativo."""
    home = Path.home()
    if sys.platform.startswith("linux"):
        xdg_music = os.environ.get("XDG_MUSIC_DIR", "").strip()
        if xdg_music:
            return Path(os.path.expandvars(xdg_music)).expanduser()
        user_dirs = home / ".config" / "user-dirs.dirs"
        if user_dirs.is_file():
            try:
                for line in user_dirs.read_text(encoding="utf-8", errors="ignore").splitlines():
                    match = re.match(r'^\s*XDG_MUSIC_DIR\s*=\s*"([^"]+)"\s*$', line)
                    if match:
                        raw_val = match.group(1).replace("$HOME", str(home))
                        return Path(raw_val).expanduser()
            except OSError:
                pass
    return home / "Music"


def default_output_dir() -> str:
    """Carpeta de salida sugerida por defecto cuando aún no se ha elegido una."""
    return str(_resolve_music_dir() / "Meta Studio Exports")


class PresetStore:
    """Gestor de lectura y escritura atómica para `profiles.json` y `genres.json`."""

    def __init__(self, presets_dir: Path | None = None) -> None:
        self.presets_dir = Path(presets_dir) if presets_dir is not None else default_presets_dir()

    @property
    def profiles_path(self) -> Path:
        return self.presets_dir / "profiles.json"

    @property
    def genres_path(self) -> Path:
        return self.presets_dir / "genres.json"

    @property
    def settings_path(self) -> Path:
        return self.presets_dir / "settings.json"

    def load_profiles(self) -> list[ProfilePreset]:
        if not self.profiles_path.is_file():
            return []
        try:
            raw = json.loads(self.profiles_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                parsed = ProfilesFile(profiles=raw)
            else:
                parsed = ProfilesFile.model_validate(raw)
            return parsed.profiles
        except (json.JSONDecodeError, ValidationError, UnicodeDecodeError):
            _backup_corrupt_json(self.profiles_path)
            return []

    def save_profiles(self, profiles: list[ProfilePreset]) -> list[ProfilePreset]:
        validated = ProfilesFile(profiles=profiles)
        _write_json_atomic(self.profiles_path, validated.model_dump(mode="json"))
        return validated.profiles

    def get_profile(self, profile_id: str) -> ProfilePreset:
        for profile in self.load_profiles():
            if profile.id == profile_id:
                return profile
        raise KeyError(f"Perfil no encontrado: {profile_id!r}")

    def upsert_profile(self, profile: ProfilePreset) -> list[ProfilePreset]:
        profiles = self.load_profiles()
        replaced = False
        for idx, existing in enumerate(profiles):
            if existing.id == profile.id:
                profiles[idx] = profile
                replaced = True
                break
        if not replaced:
            profiles.append(profile)
        return self.save_profiles(profiles)

    def delete_profile(self, profile_id: str) -> list[ProfilePreset]:
        profiles = self.load_profiles()
        remaining = [p for p in profiles if p.id != profile_id]
        if len(remaining) == len(profiles):
            raise KeyError(f"Perfil no encontrado: {profile_id!r}")
        return self.save_profiles(remaining)

    def load_genres(self) -> GenresCatalog:
        if not self.genres_path.is_file():
            return GenresCatalog()
        try:
            raw = json.loads(self.genres_path.read_text(encoding="utf-8"))
            return GenresCatalog.model_validate(raw)
        except (json.JSONDecodeError, ValidationError, UnicodeDecodeError):
            _backup_corrupt_json(self.genres_path)
            return GenresCatalog()

    def save_genres(self, catalog: GenresCatalog) -> GenresCatalog:
        validated = GenresCatalog.model_validate(catalog.model_dump())
        _write_json_atomic(self.genres_path, validated.model_dump(mode="json"))
        return validated

    def add_genre_tag(self, tag: str) -> GenresCatalog:
        catalog = self.load_genres()
        updated = GenresCatalog(
            distributor_primary=catalog.distributor_primary,
            tags=[*catalog.tags, tag],
        )
        return self.save_genres(updated)

    def remove_genre_tag(self, tag: str) -> GenresCatalog:
        catalog = self.load_genres()
        target = tag.strip()
        updated = GenresCatalog(
            distributor_primary=catalog.distributor_primary,
            tags=[t for t in catalog.tags if t != target],
        )
        return self.save_genres(updated)

    def load_settings(self) -> AppSettings:
        if not self.settings_path.is_file():
            return AppSettings(output_dir=default_output_dir())
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
            settings = AppSettings.model_validate(raw)
        except (json.JSONDecodeError, ValidationError, UnicodeDecodeError):
            _backup_corrupt_json(self.settings_path)
            return AppSettings(output_dir=default_output_dir())
        if not settings.output_dir:
            settings.output_dir = default_output_dir()
        return settings

    def save_settings(self, settings: AppSettings) -> AppSettings:
        validated = AppSettings.model_validate(settings.model_dump())
        _write_json_atomic(self.settings_path, validated.model_dump(mode="json"))
        return validated
