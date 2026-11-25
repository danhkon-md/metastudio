from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mutagen.flac import FLAC

from meta_studio.launcher import run_check
from meta_studio.services.presets import default_presets_dir
from meta_studio.web import app as web_app_module
from meta_studio.web.app import create_app
from tests.conftest import create_silent_wav, create_test_cover


@pytest.fixture
def isolated_presets_dir(tmp_path: Path) -> Path:
    dest = tmp_path / "presets"
    dest.mkdir()
    src = default_presets_dir()
    for name in ("profiles.json", "genres.json"):
        shutil.copy2(src / name, dest / name)
    return dest


@pytest.fixture
def client(isolated_presets_dir: Path) -> TestClient:
    app = create_app(presets_dir=isolated_presets_dir)
    return TestClient(app)


def test_serves_light_mode_ui_and_static_assets(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "Meta Studio" in html
    assert "Opcional: Distribución y créditos" in html
    assert "Crear copias y verificar" in html
    # Sin jerga técnica ID3 visible en la interfaz
    assert "TIT2" not in html
    assert "TXXX" not in html

    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_health_endpoint(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["ffmpeg"] is True


def test_profiles_and_genres_crud_endpoints(client: TestClient) -> None:
    # Perfiles iniciales cargados
    res = client.get("/api/presets/profiles")
    assert res.status_code == 200
    ids = [p["id"] for p in res.json()["profiles"]]
    assert ids == ["independent-artist", "demo-wip"]

    # Crear perfil
    new_profile = {
        "id": "nuevo-proyecto",
        "name": "Nuevo Proyecto",
        "artist": "Artista X",
        "album_artist": "Artista X",
        "label": "Sello X",
        "copyright": "℗ {year} Sello X",
        "release_type": "ep",
        "catalog_prefix": "SX-",
    }
    create_res = client.post("/api/presets/profiles", json=new_profile)
    assert create_res.status_code == 201
    assert any(p["id"] == "nuevo-proyecto" for p in create_res.json()["profiles"])

    # Actualizar perfil
    new_profile["label"] = "Sello Editado"
    upd_res = client.put("/api/presets/profiles/nuevo-proyecto", json=new_profile)
    assert upd_res.status_code == 200
    updated = [p for p in upd_res.json()["profiles"] if p["id"] == "nuevo-proyecto"][0]
    assert updated["label"] == "Sello Editado"

    # Eliminar perfil
    del_res = client.delete("/api/presets/profiles/nuevo-proyecto")
    assert del_res.status_code == 200
    assert all(p["id"] != "nuevo-proyecto" for p in del_res.json()["profiles"])
    assert client.delete("/api/presets/profiles/no-existe").status_code == 404

    # Géneros GET + PUT
    genres_res = client.get("/api/presets/genres")
    assert genres_res.status_code == 200
    catalog = genres_res.json()
    assert "Plunderphonics" in catalog["tags"]

    catalog["tags"].append("Shoegaze")
    catalog["tags"].append("Shoegaze")
    put_genres = client.put("/api/presets/genres", json=catalog)
    assert put_genres.status_code == 200
    assert put_genres.json()["tags"].count("Shoegaze") == 1


def test_settings_pick_folder_and_open_folder(
    client: TestClient,
    isolated_presets_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dir = tmp_path / "exports"
    target_dir.mkdir()

    # Guardar output_dir manualmente
    put_res = client.put("/api/settings/output-dir", json={"output_dir": str(target_dir)})
    assert put_res.status_code == 200
    assert client.get("/api/settings/output-dir").json()["output_dir"] == str(target_dir)
    assert (isolated_presets_dir / "settings.json").is_file()

    # Mock pick-folder exitoso -> persiste en settings.json
    picked_dir = tmp_path / "picked_exports"
    picked_dir.mkdir()
    monkeypatch.setattr(
        web_app_module,
        "_run_pick_folder",
        lambda initial_dir=None, **_kwargs: {
            "selected": True,
            "path": str(picked_dir),
            "cancelled": False,
            "supported": True,
        },
    )
    pick_res = client.post("/api/pick-folder", json={})
    assert pick_res.status_code == 200
    assert pick_res.json()["path"] == str(picked_dir)
    assert client.get("/api/settings/output-dir").json()["output_dir"] == str(picked_dir)

    # Mock pick-folder cancelado -> mantiene settings previos
    monkeypatch.setattr(
        web_app_module,
        "_run_pick_folder",
        lambda initial_dir=None, **_kwargs: {
            "selected": False,
            "path": None,
            "cancelled": True,
            "supported": True,
            "message": "Selección cancelada.",
        },
    )
    cancel_res = client.post("/api/pick-folder", json={})
    assert cancel_res.status_code == 200
    assert cancel_res.json()["cancelled"] is True
    assert client.get("/api/settings/output-dir").json()["output_dir"] == str(picked_dir)

    # Mock open-folder
    opened_paths: list[Path] = []
    monkeypatch.setattr(
        web_app_module,
        "_open_in_file_manager",
        lambda folder: opened_paths.append(folder),
    )
    open_res = client.post("/api/open-folder", json={"path": str(picked_dir)})
    assert open_res.status_code == 200
    assert opened_paths == [picked_dir]

    # open-folder con ruta inexistente -> 404
    missing_res = client.post("/api/open-folder", json={"path": str(tmp_path / "nope")})
    assert missing_res.status_code == 404


def test_clean_names_and_preview_paths(client: TestClient, tmp_path: Path) -> None:
    clean_res = client.post(
        "/api/clean-names",
        json={
            "filenames": [
                "01_my_iPhone_sketch_24bit_48kHz_final.wav",
                "02 - Lo-Fi_Rain_unmastered_v2.wav",
            ]
        },
    )
    assert clean_res.status_code == 200
    assert clean_res.json()["cleaned"] == ["my iPhone sketch", "Lo-Fi Rain"]

    preview_res = client.post(
        "/api/preview-paths",
        json={
            "album": "Tape: Vol. 1",
            "album_artist": "Studio Artist",
            "date": "2026-11-25",
            "status": "WIP",
            "output_dir": str(tmp_path / "out"),
            "wav_conversion": "flac",
            "tracks": [
                {"filename": "a.wav", "title": "Track One?", "track_number": 1, "disc_number": 1},
                {"filename": "b.wav", "title": "Bonus: Mix", "track_number": 1, "disc_number": 2},
            ],
        },
    )
    assert preview_res.status_code == 200
    data = preview_res.json()
    assert data["album_dir"] == str(tmp_path / "out" / "Studio Artist" / "[2026] Tape - Vol. 1 [WIP]")
    assert [t["output_filename"] for t in data["tracks"]] == [
        "1-01 - Track One.flac",
        "2-01 - Bonus - Mix.flac",
    ]
    assert all(t["will_convert"] is True for t in data["tracks"])


def test_process_end_to_end_and_reimport_release_info(
    client: TestClient, tmp_path: Path
) -> None:
    wav1 = create_silent_wav(tmp_path / "src" / "01_opening_24bit_master.wav", duration_seconds=0.5)
    wav2 = create_silent_wav(tmp_path / "src" / "02_closing_48kHz_final.wav", duration_seconds=0.5)
    cover = create_test_cover(tmp_path / "src" / "cover.png", size=(1600, 1600))
    export_root = tmp_path / "exports"

    payload = {
        "album": "Glass & Tape",
        "album_artist": "Studio Artist",
        "artist": "Studio Artist",
        "date": "2026-11-25",
        "genre": ["Ambient", "Tape Loops", "Lo-Fi"],
        "release_type": "album",
        "status": "Final",
        "label": "Independent Label",
        "catalog_number": "CAT-020",
        "copyright": "℗ 2026 Independent Label",
        "distribution": {
            "primary_genre": "Electronic",
            "secondary_genre": "Ambient",
            "language": "Instrumental",
            "instrumental": True,
            "explicit": False,
            "upc": "198009999999",
            "credits": "Produced by Studio Artist.\nMastered in house.",
        },
        "output_dir": str(export_root),
        "wav_conversion": "flac",
        "tracks": [
            {
                "title": "opening",
                "artist": "Studio Artist",
                "track_number": 1,
                "disc_number": 1,
                "isrc": "USXX02600101",
                "original_filename": wav1.name,
            },
            {
                "title": "closing",
                "artist": "Studio Artist",
                "track_number": 2,
                "disc_number": 1,
                "isrc": "USXX02600102",
                "original_filename": wav2.name,
            },
        ],
    }

    res = client.post(
        "/api/process",
        data={"payload": json.dumps(payload)},
        files=[
            ("audio_files", (wav1.name, wav1.read_bytes(), "audio/wav")),
            ("audio_files", (wav2.name, wav2.read_bytes(), "audio/wav")),
            ("cover_file", (cover.name, cover.read_bytes(), "image/png")),
        ],
    )
    assert res.status_code == 200, res.text
    result = res.json()

    assert result["all_verified"] is True
    album_dir = Path(result["album_dir"])
    assert album_dir == export_root / "Studio Artist" / "[2026] Glass & Tape"
    assert (album_dir / "cover.jpg").is_file()
    assert (album_dir / "release-info.json").is_file()
    assert (album_dir / "release-info.md").is_file()

    # Verificar multi-género real en el FLAC resultante
    flac1 = FLAC(album_dir / "01 - opening.flac")
    assert flac1["GENRE"] == ["Ambient", "Tape Loops", "Lo-Fi"]
    assert flac1["LABEL"] == ["Independent Label"]
    assert flac1["CATALOGNUMBER"] == ["CAT-020"]

    # Última carpeta persistida automáticamente
    assert client.get("/api/settings/output-dir").json()["output_dir"] == str(export_root)

    # Reimportar release-info.md y release-info.json vía API
    md_bytes = (album_dir / "release-info.md").read_bytes()
    json_bytes = (album_dir / "release-info.json").read_bytes()

    imp_md = client.post(
        "/api/import-release-info",
        files={"file": ("release-info.md", md_bytes, "text/markdown")},
    )
    imp_json = client.post(
        "/api/import-release-info",
        files={"file": ("release-info.json", json_bytes, "application/json")},
    )
    assert imp_md.status_code == 200
    assert imp_json.status_code == 200
    assert imp_md.json() == imp_json.json()
    assert imp_md.json()["distribution"]["upc"] == "198009999999"
    assert [t["isrc"] for t in imp_md.json()["tracks"]] == ["USXX02600101", "USXX02600102"]


def test_launcher_check_passes() -> None:
    assert run_check() == 0
