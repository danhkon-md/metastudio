from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from mutagen import MutagenError
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from meta_studio.core.converter import ConversionError, is_ffmpeg_available
from meta_studio.core.models import (
    DistributionInfo,
    ReleaseInput,
    ReleaseStatus,
    TrackInput,
    WavConversionMode,
)
from meta_studio.core.pipeline import process_release
from meta_studio.core.sanitizer import build_album_dir, build_track_filename
from meta_studio.core.tagger import TaggingError, resolve_target_format
from meta_studio.runtime import get_static_dir
from meta_studio.services.cleaner import clean_track_titles
from meta_studio.services.presets import (
    AppSettings,
    GenresCatalog,
    PresetStore,
    ProfilePreset,
)
from meta_studio.services.release_info import (
    parse_release_info_json,
    parse_release_info_md,
)

STATIC_DIR = get_static_dir()
_STREAM_CHUNK_SIZE = 1024 * 1024  # 1 MB
_MAX_RELEASE_INFO_BYTES = 5 * 1024 * 1024  # 5 MB
_PICK_FOLDER_TIMEOUT_SECONDS = 120.0
_ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class CleanNamesRequest(BaseModel):
    filenames: list[str] = Field(default_factory=list)


class PreviewTrackItem(BaseModel):
    filename: str
    title: str
    track_number: int = Field(ge=1)
    disc_number: int = Field(default=1, ge=1)


class PreviewPathsRequest(BaseModel):
    album: str = ""
    album_artist: str = ""
    date: str = ""
    status: ReleaseStatus = "Final"
    output_dir: str = ""
    wav_conversion: WavConversionMode = "keep"
    tracks: list[PreviewTrackItem] = Field(default_factory=list)


class PickFolderRequest(BaseModel):
    initial_dir: str | None = None


class OpenFolderRequest(BaseModel):
    path: str


class ProcessTrackPayload(BaseModel):
    title: str
    artist: str | None = None
    track_number: int = Field(ge=1)
    disc_number: int = Field(default=1, ge=1)
    genre: list[str] | None = None
    isrc: str | None = None
    original_filename: str | None = None


class ProcessReleasePayload(BaseModel):
    album: str
    album_artist: str
    artist: str | None = None
    date: str
    original_date: str | None = None
    genre: list[str]
    release_type: str
    status: ReleaseStatus = "Final"
    label: str | None = None
    catalog_number: str | None = None
    copyright: str | None = None
    distribution: DistributionInfo = Field(default_factory=DistributionInfo)
    output_dir: str
    wav_conversion: WavConversionMode = "keep"
    tracks: list[ProcessTrackPayload] = Field(min_length=1)


async def _stream_upload_to_path(upload: UploadFile, dest_path: Path) -> int:
    """Escribe un `UploadFile` en streaming por bloques de 1 MB sin cargarlo entero en RAM."""
    total_bytes = 0
    with dest_path.open("wb") as out_fh:
        while True:
            chunk = await upload.read(_STREAM_CHUNK_SIZE)
            if not chunk:
                break
            out_fh.write(chunk)
            total_bytes += len(chunk)
    return total_bytes


def _is_allowed_local_origin(origin: str) -> bool:
    """Valida que el header Origin corresponda exclusivamente a loopback local."""
    parsed = urlparse(origin.strip())
    if parsed.scheme != "http":
        return False
    return (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}


def _run_macos_pick_folder(
    initial_dir: str | None = None,
    *,
    timeout_seconds: float = _PICK_FOLDER_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Abre el diálogo nativo de macOS vía subproceso `osascript` pasando la ruta por `argv`."""
    if sys.platform != "darwin":
        return {
            "selected": False,
            "path": None,
            "cancelled": False,
            "supported": False,
            "message": "El selector nativo está disponible solo en macOS. Escribe la ruta manualmente.",
        }

    script = (
        "on run argv\n"
        '  set promptText to "Selecciona la carpeta de salida para Meta Studio"\n'
        "  activate\n"
        "  if (count of argv) > 0 then\n"
        "    set startLoc to POSIX file (item 1 of argv)\n"
        "    return POSIX path of (choose folder with prompt promptText default location startLoc)\n"
        "  else\n"
        "    return POSIX path of (choose folder with prompt promptText)\n"
        "  end if\n"
        "end run"
    )

    cmd = ["osascript", "-e", script]
    if initial_dir and initial_dir.strip():
        expanded = Path(initial_dir.strip()).expanduser()
        if expanded.is_dir():
            cmd.extend(["--", str(expanded)])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "selected": False,
            "path": None,
            "cancelled": True,
            "supported": True,
            "message": "El selector de carpeta expiró por tiempo de espera.",
        }
    except FileNotFoundError:
        return {
            "selected": False,
            "path": None,
            "cancelled": False,
            "supported": False,
            "message": "osascript no está disponible. Escribe la ruta manualmente.",
        }

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        cancelled = "-128" in stderr or "User canceled" in stderr
        return {
            "selected": False,
            "path": None,
            "cancelled": cancelled,
            "supported": True,
            "message": "Selección cancelada." if cancelled else (stderr or "No se pudo abrir el selector."),
        }

    chosen = proc.stdout.strip()
    if chosen != "/":
        chosen = chosen.rstrip("/")
    return {
        "selected": bool(chosen),
        "path": chosen or None,
        "cancelled": False,
        "supported": True,
    }


def _run_windows_pick_folder(
    initial_dir: str | None = None,
    *,
    timeout_seconds: float = _PICK_FOLDER_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Abre el selector nativo de carpeta en Windows vía PowerShell `FolderBrowserDialog`."""
    ps_bin = shutil.which("powershell") or shutil.which("pwsh")
    if not ps_bin:
        return {
            "selected": False,
            "path": None,
            "cancelled": False,
            "supported": False,
            "message": "PowerShell no está disponible. Escribe la ruta manualmente.",
        }

    ps_script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$OutputEncoding=[System.Text.Encoding]::UTF8; "
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dialog.Description = 'Selecciona la carpeta de salida para Meta Studio'; "
        "$dialog.UseDescriptionForTitle = $true; "
        "if ($env:META_STUDIO_INITIAL_DIR -and (Test-Path -LiteralPath $env:META_STUDIO_INITIAL_DIR)) { "
        "  $dialog.SelectedPath = $env:META_STUDIO_INITIAL_DIR "
        "}; "
        "$result = $dialog.ShowDialog(); "
        "if ($result -eq [System.Windows.Forms.DialogResult]::OK) { "
        "  [Console]::Out.Write($dialog.SelectedPath) "
        "} else { exit 2 }"
    )

    env = os.environ.copy()
    if initial_dir and initial_dir.strip():
        expanded = Path(initial_dir.strip()).expanduser()
        if expanded.is_dir():
            env["META_STUDIO_INITIAL_DIR"] = str(expanded)

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            [ps_bin, "-NoProfile", "-STA", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
            env=env,
            creationflags=creation_flags,
        )
    except subprocess.TimeoutExpired:
        return {
            "selected": False,
            "path": None,
            "cancelled": True,
            "supported": True,
            "message": "El selector de carpeta expiró por tiempo de espera.",
        }
    except FileNotFoundError:
        return {
            "selected": False,
            "path": None,
            "cancelled": False,
            "supported": False,
            "message": "PowerShell no está disponible. Escribe la ruta manualmente.",
        }

    if proc.returncode == 2:
        return {
            "selected": False,
            "path": None,
            "cancelled": True,
            "supported": True,
            "message": "Selección cancelada.",
        }
    if proc.returncode != 0:
        return {
            "selected": False,
            "path": None,
            "cancelled": False,
            "supported": True,
            "message": (proc.stderr or "").strip() or "No se pudo abrir el selector.",
        }

    chosen = proc.stdout.strip()
    return {
        "selected": bool(chosen),
        "path": chosen or None,
        "cancelled": not bool(chosen),
        "supported": True,
    }


def _run_linux_pick_folder(
    initial_dir: str | None = None,
    *,
    timeout_seconds: float = _PICK_FOLDER_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Abre el selector nativo en Linux usando `zenity` o `kdialog` con fallback manual."""
    start_dir: str | None = None
    if initial_dir and initial_dir.strip():
        expanded = Path(initial_dir.strip()).expanduser()
        if expanded.is_dir():
            start_dir = str(expanded)

    zenity = shutil.which("zenity")
    kdialog = shutil.which("kdialog")

    if zenity:
        cmd = [
            zenity,
            "--file-selection",
            "--directory",
            "--title=Selecciona la carpeta de salida para Meta Studio",
        ]
        if start_dir:
            cmd.append(f"--filename={start_dir.rstrip('/')}/")
    elif kdialog:
        cmd = [
            kdialog,
            "--getexistingdirectory",
            start_dir or str(Path.home()),
            "--title",
            "Selecciona la carpeta de salida para Meta Studio",
        ]
    else:
        return {
            "selected": False,
            "path": None,
            "cancelled": False,
            "supported": False,
            "message": "No se encontró zenity ni kdialog. Escribe la ruta manualmente.",
        }

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "selected": False,
            "path": None,
            "cancelled": True,
            "supported": True,
            "message": "El selector de carpeta expiró por tiempo de espera.",
        }

    if proc.returncode != 0:
        return {
            "selected": False,
            "path": None,
            "cancelled": True,
            "supported": True,
            "message": "Selección cancelada.",
        }

    chosen = proc.stdout.strip()
    if chosen != "/":
        chosen = chosen.rstrip("/")
    return {
        "selected": bool(chosen),
        "path": chosen or None,
        "cancelled": not bool(chosen),
        "supported": True,
    }


def _run_pick_folder(
    initial_dir: str | None = None,
    *,
    timeout_seconds: float = _PICK_FOLDER_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Despacha el selector nativo según sistema operativo con fallback manual limpio."""
    if sys.platform == "darwin":
        return _run_macos_pick_folder(
            initial_dir=initial_dir, timeout_seconds=timeout_seconds
        )
    if sys.platform == "win32":
        return _run_windows_pick_folder(
            initial_dir=initial_dir, timeout_seconds=timeout_seconds
        )
    if sys.platform.startswith("linux"):
        return _run_linux_pick_folder(
            initial_dir=initial_dir, timeout_seconds=timeout_seconds
        )
    return {
        "selected": False,
        "path": None,
        "cancelled": False,
        "supported": False,
        "message": "Selector nativo no disponible en esta plataforma. Escribe la ruta manualmente.",
    }


def _open_in_file_manager(folder: Path) -> None:
    """Abre una carpeta en Finder (macOS), Explorer (Windows) o gestor de archivos (Linux)."""
    if sys.platform == "darwin":
        subprocess.run(["open", "--", str(folder)], check=True, capture_output=True, text=True)
        return
    if sys.platform == "win32":
        os.startfile(str(folder))  # type: ignore[attr-defined]
        return
    if sys.platform.startswith("linux"):
        subprocess.run(
            ["xdg-open", str(folder.resolve())],
            check=True,
            capture_output=True,
            text=True,
        )
        return
    raise RuntimeError(f"Plataforma no soportada para abrir carpetas: {sys.platform}")


def create_app(presets_dir: Path | None = None) -> FastAPI:
    store = PresetStore(presets_dir=presets_dir)
    static_dir = get_static_dir()
    app = FastAPI(title="Meta Studio", version="1.0.0")
    app.state.preset_store = store
    # Se mantiene en None (sin llamadas a Cocoa fuera del hilo principal)
    app.state.native_pick_folder = None

    @app.middleware("http")
    async def local_only_guard(request: Request, call_next):
        client_host = request.client.host if request.client else ""
        if client_host and client_host not in _ALLOWED_LOCAL_HOSTS:
            return JSONResponse(
                status_code=403,
                content={"detail": "Acceso permitido únicamente desde localhost"},
            )
        origin = request.headers.get("origin")
        if origin and not _is_allowed_local_origin(origin):
            return JSONResponse(
                status_code=403,
                content={"detail": "Origen no permitido (solo localhost)"},
            )
        return await call_next(request)

    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        index_path = static_dir / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=404, detail="Interfaz web no encontrada")
        return FileResponse(index_path, headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": "1.0.0",
            "ffmpeg": is_ffmpeg_available(),
            "platform": sys.platform,
        }

    # --- Presets: Profiles ---

    @app.get("/api/presets/profiles")
    def list_profiles() -> dict[str, object]:
        profiles = store.load_profiles()
        return {"profiles": [p.model_dump(mode="json") for p in profiles]}

    @app.post("/api/presets/profiles", status_code=201)
    def create_or_upsert_profile(profile: ProfilePreset) -> dict[str, object]:
        profiles = store.upsert_profile(profile)
        return {
            "profile": profile.model_dump(mode="json"),
            "profiles": [p.model_dump(mode="json") for p in profiles],
        }

    @app.put("/api/presets/profiles/{profile_id}")
    def update_profile(profile_id: str, profile: ProfilePreset) -> dict[str, object]:
        if profile.id != profile_id.strip().lower():
            raise HTTPException(
                status_code=400,
                detail="El ID del perfil en el cuerpo debe coincidir con la URL",
            )
        profiles = store.upsert_profile(profile)
        return {
            "profile": profile.model_dump(mode="json"),
            "profiles": [p.model_dump(mode="json") for p in profiles],
        }

    @app.delete("/api/presets/profiles/{profile_id}")
    def delete_profile(profile_id: str) -> dict[str, object]:
        try:
            profiles = store.delete_profile(profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "deleted": profile_id,
            "profiles": [p.model_dump(mode="json") for p in profiles],
        }

    # --- Presets: Genres ---

    @app.get("/api/presets/genres")
    def get_genres() -> dict[str, object]:
        return store.load_genres().model_dump(mode="json")

    @app.put("/api/presets/genres")
    def put_genres(catalog: GenresCatalog) -> dict[str, object]:
        saved = store.save_genres(catalog)
        return saved.model_dump(mode="json")

    # --- Settings: Default Output Dir ---

    @app.get("/api/settings/output-dir")
    def get_output_dir() -> dict[str, str]:
        return store.load_settings().model_dump(mode="json")

    @app.put("/api/settings/output-dir")
    def put_output_dir(settings: AppSettings) -> dict[str, str]:
        saved = store.save_settings(settings)
        return saved.model_dump(mode="json")

    # --- Native macOS Folder Helpers ---

    @app.post("/api/pick-folder")
    def pick_folder(req: PickFolderRequest | None = None) -> dict[str, object]:
        initial = req.initial_dir if req and req.initial_dir else store.load_settings().output_dir
        native_picker = getattr(app.state, "native_pick_folder", None)
        if callable(native_picker):
            result = native_picker(initial)
        else:
            result = _run_pick_folder(initial_dir=initial)
        chosen = result.get("path")
        if result.get("selected") and isinstance(chosen, str) and chosen:
            store.save_settings(AppSettings(output_dir=chosen))
        return result

    @app.post("/api/open-folder")
    def open_folder(req: OpenFolderRequest) -> dict[str, object]:
        raw_path = req.path.strip()
        if not raw_path:
            raise HTTPException(status_code=400, detail="La ruta de carpeta está vacía")
        target = Path(raw_path).expanduser()
        if not target.is_dir():
            raise HTTPException(
                status_code=404, detail=f"La carpeta no existe: {target}"
            )
        try:
            _open_in_file_manager(target)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"No se pudo abrir la carpeta: {exc}"
            ) from exc
        return {"opened": True, "path": str(target)}

    # --- Utilities: Clean Names & Preview Paths ---

    @app.post("/api/clean-names")
    def clean_names(req: CleanNamesRequest) -> dict[str, list[str]]:
        return {"cleaned": clean_track_titles(req.filenames)}

    @app.post("/api/preview-paths")
    def preview_paths(req: PreviewPathsRequest) -> dict[str, object]:
        output_base = Path(req.output_dir.strip() or store.load_settings().output_dir).expanduser()
        year = req.date.strip()[:4] if len(req.date.strip()) >= 4 else "YYYY"
        album_artist = req.album_artist.strip() or "Artista"
        album = req.album.strip() or "Álbum"

        try:
            album_dir = build_album_dir(
                output_dir=output_base,
                album_artist=album_artist,
                year=year,
                album=album,
                status=req.status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        total_discs = max((t.disc_number for t in req.tracks), default=1)
        preview_tracks: list[dict[str, object]] = []
        for item in req.tracks:
            try:
                target_ext, will_convert = resolve_target_format(
                    Path(item.filename), req.wav_conversion
                )
            except TaggingError:
                raw_ext = Path(item.filename).suffix.lstrip(".").lower() or "flac"
                target_ext, will_convert = raw_ext, False  # type: ignore[assignment]

            safe_title = item.title.strip() or "Sin título"
            try:
                out_name = build_track_filename(
                    track_number=item.track_number,
                    title=safe_title,
                    extension=target_ext,
                    disc_number=item.disc_number,
                    total_discs=total_discs,
                    parent_dir=album_dir,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            preview_tracks.append(
                {
                    "track_number": item.track_number,
                    "disc_number": item.disc_number,
                    "output_filename": out_name,
                    "output_path": str(album_dir / out_name),
                    "target_format": target_ext,
                    "will_convert": will_convert,
                }
            )

        return {
            "album_dir": str(album_dir),
            "tracks": preview_tracks,
        }

    # --- Import release-info (.json / .md) ---

    @app.post("/api/import-release-info")
    async def import_release_info(file: UploadFile = File(...)) -> dict[str, object]:
        raw_bytes = await file.read(_MAX_RELEASE_INFO_BYTES + 1)
        if len(raw_bytes) > _MAX_RELEASE_INFO_BYTES:
            raise HTTPException(
                status_code=413,
                detail="El archivo release-info excede el tamaño máximo permitido (5 MB)",
            )
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=400, detail="El archivo debe estar codificado en UTF-8"
            ) from exc

        filename = (file.filename or "").lower()
        try:
            if filename.endswith(".json") or text.lstrip().startswith("{"):
                doc = parse_release_info_json(text)
            elif filename.endswith(".md") or "## Identidad" in text:
                doc = parse_release_info_md(text)
            else:
                raise ValueError("Solo se aceptan archivos release-info.json o release-info.md")
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"No se pudo importar release-info: {exc}"
            ) from exc

        return doc.model_dump(mode="json")

    # --- Process & Verify Release (Copy-First + Atomic + Threadpool + Streaming) ---

    @app.post("/api/process")
    async def process_endpoint(
        payload: str = Form(...),
        audio_files: list[UploadFile] = File(...),
        cover_file: UploadFile | None = File(default=None),
    ) -> dict[str, object]:
        try:
            parsed_payload = ProcessReleasePayload.model_validate(json.loads(payload))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail=f"Datos inválidos: {exc}") from exc

        raw_output_dir = parsed_payload.output_dir.strip()
        if not raw_output_dir:
            raise HTTPException(
                status_code=400, detail="Debes indicar una carpeta de salida"
            )
        output_dir = Path(raw_output_dir).expanduser()

        if len(audio_files) != len(parsed_payload.tracks):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cantidad de archivos de audio ({len(audio_files)}) "
                    f"no coincide con tracks ({len(parsed_payload.tracks)})"
                ),
            )

        with tempfile.TemporaryDirectory(prefix="meta_studio_stage_") as tmp_dir_str:
            stage_dir = Path(tmp_dir_str)
            staged_tracks: list[TrackInput] = []

            for idx, (upload, track_meta) in enumerate(
                zip(audio_files, parsed_payload.tracks, strict=True), start=1
            ):
                orig_name = upload.filename or track_meta.original_filename or f"track_{idx}.wav"
                ext = Path(orig_name).suffix.lower()
                if ext not in {".flac", ".mp3", ".wav"}:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Formato no soportado en {orig_name!r}. Usa WAV, FLAC o MP3.",
                    )
                staged_audio = stage_dir / f"src_{idx:03d}{ext}"
                try:
                    written_size = await _stream_upload_to_path(upload, staged_audio)
                except OSError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"No se pudo recibir el archivo {orig_name!r}: {exc}",
                    ) from exc
                if written_size == 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"El archivo de audio {orig_name!r} está vacío.",
                    )
                staged_tracks.append(
                    TrackInput(
                        source_path=staged_audio,
                        title=track_meta.title,
                        artist=track_meta.artist,
                        track_number=track_meta.track_number,
                        disc_number=track_meta.disc_number,
                        genre=track_meta.genre,
                        isrc=track_meta.isrc,
                    )
                )

            staged_cover: Path | None = None
            if cover_file is not None and cover_file.filename:
                cover_ext = Path(cover_file.filename).suffix.lower() or ".img"
                candidate_cover = stage_dir / f"cover_input{cover_ext}"
                try:
                    cover_size = await _stream_upload_to_path(cover_file, candidate_cover)
                except OSError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"No se pudo leer la portada: {exc}",
                    ) from exc
                if cover_size > 0:
                    staged_cover = candidate_cover

            try:
                release_input = ReleaseInput(
                    album=parsed_payload.album,
                    album_artist=parsed_payload.album_artist,
                    artist=parsed_payload.artist,
                    date=parsed_payload.date,
                    original_date=parsed_payload.original_date,
                    genre=parsed_payload.genre,
                    release_type=parsed_payload.release_type,
                    status=parsed_payload.status,
                    label=parsed_payload.label,
                    catalog_number=parsed_payload.catalog_number,
                    copyright=parsed_payload.copyright,
                    distribution=parsed_payload.distribution,
                    output_dir=output_dir,
                    cover_path=staged_cover,
                    wav_conversion=parsed_payload.wav_conversion,
                    tracks=staged_tracks,
                )
            except ValidationError as exc:
                raise HTTPException(
                    status_code=422, detail=f"Validación de metadata falló: {exc}"
                ) from exc

            try:
                result = await run_in_threadpool(
                    process_release,
                    release_input,
                    raise_on_verification_error=False,
                )
            except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"La imagen de portada es inválida o demasiado grande: {exc}",
                ) from exc
            except MutagenError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Archivo de audio dañado o ilegible: {exc}",
                ) from exc
            except (ConversionError, TaggingError, ValueError, OSError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Persistir última carpeta de salida usada
        store.save_settings(AppSettings(output_dir=str(output_dir)))

        return {
            "all_verified": result.all_verified,
            "album_dir": str(result.album_dir),
            "cover_path": str(result.cover_path) if result.cover_path else None,
            "release_info_json_path": (
                str(result.release_info_json_path)
                if result.release_info_json_path
                else None
            ),
            "release_info_md_path": (
                str(result.release_info_md_path)
                if result.release_info_md_path
                else None
            ),
            "tracks": [
                {
                    "track_number": item.metadata.track_number,
                    "total_tracks": item.metadata.total_tracks,
                    "disc_number": item.metadata.disc_number,
                    "title": item.metadata.title,
                    "artist": item.metadata.artist,
                    "format": item.format,
                    "converted": item.converted,
                    "filename": item.output_path.name,
                    "output_path": str(item.output_path),
                    "duration_seconds": round(item.verification.duration_seconds, 2),
                    "verified": item.verification.passed,
                    "has_cover": item.verification.has_cover,
                    "cover_matches": item.verification.cover_matches,
                    "mismatches": [
                        m.model_dump(mode="json") for m in item.verification.mismatches
                    ],
                }
                for item in result.tracks
            ],
        }

    return app


app = create_app()
