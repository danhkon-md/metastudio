from __future__ import annotations

import json
import os
import stat
import subprocess
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TIT2, Encoding
from mutagen.wave import WAVE

from meta_studio.core.fs import cleanup_orphan_temps
from meta_studio.core.models import (
    DistributionInfo,
    ReleaseInput,
    TrackInput,
    TrackMetadata,
    VerificationMismatch,
    VerificationResult,
)
from meta_studio.core.pipeline import process_release
from meta_studio.core.sanitizer import (
    MAX_COMPONENT_UTF8_BYTES,
    MAX_SAFE_FULL_PATH_CHARS,
    build_album_dir,
    build_track_filename,
    sanitize_name,
)
from meta_studio.core.tagger import write_tagged_copy
from meta_studio.core.verifier import VerificationError, verify_audio_file
from meta_studio.desktop import LocalUvicornServer
from meta_studio.services.cleaner import clean_track_title
from meta_studio.services.presets import PresetStore
from meta_studio.services.release_info import (
    ReleaseInfoDocument,
    ReleaseInfoTrack,
    parse_release_info_md,
    render_release_info_md,
)
from meta_studio.web import app as web_app_module
from meta_studio.web.app import create_app
from tests.conftest import create_silent_flac, create_silent_wav, create_test_cover


def _make_meta(*, total_discs: int = 2, disc_number: int = 1) -> TrackMetadata:
    return TrackMetadata(
        title="Signal",
        artist="Studio Artist",
        album="Tape",
        album_artist="Studio Artist",
        date="2026-11-25",
        track_number=1,
        total_tracks=1,
        disc_number=disc_number,
        total_discs=total_discs,
        genre=["Ambient", "Drone"],
        release_type="album",
    )


def test_verify_before_publish_preserves_existing_file_and_cleans_staged_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = create_silent_flac(tmp_path / "src" / "01.flac", duration_seconds=0.5)
    out_root = tmp_path / "exports"
    album_dir = out_root / "Studio Artist" / "[2026] Tape"
    album_dir.mkdir(parents=True)
    existing_dest = album_dir / "01 - Signal.flac"
    sentinel_bytes = b"EXISTING-GOOD-FILE-DO-NOT-OVERWRITE"
    existing_dest.write_bytes(sentinel_bytes)

    def _fake_fail(file_path: Path, *_args, **_kwargs) -> VerificationResult:
        # Debe recibir el temporal staged, NO el archivo destino final
        assert file_path != existing_dest
        assert file_path.name.startswith(".tmp_meta_")
        return VerificationResult(
            file_path=file_path,
            passed=False,
            duration_seconds=0.5,
            has_cover=False,
            cover_matches=True,
            mismatches=[
                VerificationMismatch(field="TITLE", expected="Signal", actual="CORRUPT")
            ],
        )

    monkeypatch.setattr("meta_studio.core.pipeline.verify_audio_file", _fake_fail)

    release = ReleaseInput(
        album="Tape",
        album_artist="Studio Artist",
        date="2026-11-25",
        genre=["Ambient"],
        release_type="album",
        output_dir=out_root,
        tracks=[TrackInput(source_path=src, title="Signal", track_number=1)],
    )

    with pytest.raises(VerificationError):
        process_release(release)

    # El archivo previo sigue intacto y no quedó ningún .tmp_meta_* residual
    assert existing_dest.read_bytes() == sentinel_bytes
    assert [p.name for p in album_dir.iterdir()] == ["01 - Signal.flac"]


def test_prevalidation_invalid_cover_does_not_create_album_dir(tmp_path: Path) -> None:
    src = create_silent_flac(tmp_path / "src" / "01.flac", duration_seconds=0.5)
    bad_cover = tmp_path / "src" / "bad.jpg"
    bad_cover.write_bytes(b"not a real image")
    out_root = tmp_path / "exports"

    release = ReleaseInput(
        album="NeverCreated",
        album_artist="Studio Artist",
        date="2026-11-25",
        genre=["Ambient"],
        release_type="album",
        output_dir=out_root,
        cover_path=bad_cover,
        tracks=[TrackInput(source_path=src, title="Track", track_number=1)],
    )

    with pytest.raises(Exception):
        process_release(release)

    assert not (out_root / "Studio Artist").exists()


def test_wav_strips_id3v1_trailer_and_verifies_totaldiscs(tmp_path: Path) -> None:
    src_wav = create_silent_wav(tmp_path / "src" / "song.wav", duration_seconds=0.5)
    # Inyectar un bloque ID3v1 de 128 bytes al final del WAV
    with src_wav.open("ab") as fh:
        fh.write(b"TAG" + b"\x00" * 125)

    dest_wav = tmp_path / "out" / "1-01 - Signal.wav"
    meta = _make_meta(total_discs=2, disc_number=1)
    write_tagged_copy(src_wav, dest_wav, meta, wav_conversion="keep")

    raw = dest_wav.read_bytes()
    assert raw[-128:-125] != b"TAG"

    reopened = WAVE(dest_wav)
    assert reopened.tags is not None
    assert reopened.tags.version == (2, 4, 0)
    assert str(reopened.tags.getall("TPOS")[0].text[0]) == "1/2"

    verification = verify_audio_file(dest_wav, meta)
    assert verification.passed is True
    assert verification.read_tags["TOTALDISCS"] == "2"

    # Si alguien agrega un bloque ID3v1 después, el verificador debe detectarlo
    with dest_wav.open("ab") as fh:
        fh.write(b"TAG" + b"\x00" * 125)
    failed = verify_audio_file(dest_wav, meta)
    assert failed.passed is False
    assert any(m.field == "ID3V1_TRAILER" for m in failed.mismatches)


def test_flac_strips_leading_id3_header_and_verifies_totaldiscs(tmp_path: Path) -> None:
    src_flac = create_silent_flac(tmp_path / "src" / "song.flac", duration_seconds=0.5)
    # Anteponer una cabecera ID3v2 real al archivo FLAC
    id3 = ID3()
    id3.add(TIT2(encoding=Encoding.UTF8, text=["BAD ID3 ON FLAC"]))
    id3.save(src_flac, v1=0, v2_version=3)
    assert src_flac.read_bytes()[:3] == b"ID3"

    dest_flac = tmp_path / "out" / "1-01 - Signal.flac"
    meta = _make_meta(total_discs=2, disc_number=1)
    write_tagged_copy(src_flac, dest_flac, meta)

    assert dest_flac.read_bytes()[:4] == b"fLaC"
    reopened = FLAC(dest_flac)
    assert reopened["TOTALDISCS"] == ["2"]

    verification = verify_audio_file(dest_flac, meta)
    assert verification.passed is True
    assert verification.read_tags["TOTALDISCS"] == "2"

    # Si se vuelve a anteponer ID3v2, el verificador lo detecta
    id3.save(dest_flac, v1=0, v2_version=3)
    failed = verify_audio_file(dest_flac, meta)
    assert failed.passed is False
    assert any(m.field == "FLAC_ID3_HEADER" for m in failed.mismatches)


def test_readonly_0444_source_produces_0644_outputs(tmp_path: Path) -> None:
    src = create_silent_flac(tmp_path / "src" / "readonly.flac", duration_seconds=0.5)
    cover = create_test_cover(tmp_path / "src" / "art.png", size=(600, 600))
    os.chmod(src, 0o444)

    try:
        release = ReleaseInput(
            album="Permissions",
            album_artist="Studio Artist",
            date="2026-11-25",
            genre=["Ambient"],
            release_type="album",
            output_dir=tmp_path / "exports",
            cover_path=cover,
            tracks=[TrackInput(source_path=src, title="ReadOnly Source", track_number=1)],
        )
        result = process_release(release)
        assert result.all_verified is True

        # Salidas en 0644 en POSIX (o escribibles en Windows)
        for produced in [
            result.tracks[0].output_path,
            result.cover_path,
            result.release_info_json_path,
            result.release_info_md_path,
        ]:
            assert produced is not None
            if os.name == "nt":
                assert os.access(produced, os.W_OK)
            else:
                mode = stat.S_IMODE(produced.stat().st_mode)
                assert mode == 0o644

        # El origen sigue siendo solo lectura intacto
        if os.name != "nt":
            assert stat.S_IMODE(src.stat().st_mode) == 0o444
    finally:
        os.chmod(src, 0o644)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("99 Luftballons.wav", "99 Luftballons"),
        ("7 Rings.wav", "7 Rings"),
        ("The Master.wav", "The Master"),
        ("Final Mix.wav", "Final Mix"),
        ("My Demo.wav", "My Demo"),
        ("01 Intro.wav", "Intro"),
        ("01 - The Master_final_v2.wav", "The Master"),
        ("99_Luftballons_24bit_48kHz_export.flac", "Luftballons"),
        ("03 - Song - render.wav", "Song"),
    ],
)
def test_cleaner_regression_numeric_titles_and_ambiguous_words(
    raw: str, expected: str
) -> None:
    assert clean_track_title(raw) == expected


def test_sanitizer_strips_control_chars_and_limits_utf8_bytes_in_nfc() -> None:
    with_ctrl = "Hello\x00\x07\x1fWorld\x7f"
    assert sanitize_name(with_ctrl) == "HelloWorld"

    long_nfd = unicodedata.normalize("NFD", "é" * 150)
    sanitized = sanitize_name(long_nfd)
    assert unicodedata.is_normalized("NFC", sanitized)
    assert len(sanitized.encode("utf-8")) <= MAX_COMPONENT_UTF8_BYTES
    assert len(sanitized) > 50


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CON", "_CON"),
        ("nul", "_nul"),
        ("PRN", "_PRN"),
        ("AUX", "_AUX"),
        ("COM1", "_COM1"),
        ("com9", "_com9"),
        ("LPT1", "_LPT1"),
        ("lpt9", "_lpt9"),
        ("CON.txt", "_CON.txt"),
        ("nul.backup", "_nul.backup"),
        ("Console", "Console"),
    ],
)
def test_sanitizer_escapes_windows_reserved_device_names(
    raw: str, expected: str
) -> None:
    assert sanitize_name(raw) == expected


def test_sanitizer_clamps_combined_album_and_track_path_for_windows_max_path(
    tmp_path: Path,
) -> None:
    base = tmp_path / ("deep_folder_" * 4)
    album_dir = build_album_dir(
        base,
        "A" * 120,
        "2026",
        "B" * 120,
        status="Demo",
    )
    filename = build_track_filename(
        1,
        "C" * 140,
        "flac",
        parent_dir=album_dir,
    )
    full_path = album_dir / filename
    assert len(str(full_path)) <= MAX_SAFE_FULL_PATH_CHARS


def test_import_release_info_rejects_oversized_payload(tmp_path: Path) -> None:
    app = create_app(presets_dir=tmp_path / "presets")
    client = TestClient(app)

    oversized = b"x" * (5 * 1024 * 1024 + 16)
    res = client.post(
        "/api/import-release-info",
        files={"file": ("release-info.json", oversized, "application/json")},
    )
    assert res.status_code == 413


def test_corrupt_preset_json_is_backed_up_and_recovers_safely(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    store.profiles_path.write_text("{corrupt json", encoding="utf-8")
    store.genres_path.write_text("[not valid", encoding="utf-8")
    store.settings_path.write_text("{bad settings", encoding="utf-8")

    assert store.load_profiles() == []
    genres = store.load_genres()
    assert genres.tags == []
    settings = store.load_settings()
    assert bool(settings.output_dir)

    backups = sorted(p.name for p in tmp_path.glob("*.corrupt-*"))
    assert len(backups) == 3
    assert any(name.startswith("profiles.json.corrupt-") for name in backups)
    assert any(name.startswith("genres.json.corrupt-") for name in backups)
    assert any(name.startswith("settings.json.corrupt-") for name in backups)


def test_release_info_markdown_protects_headings_in_credits_and_literal_em_dash() -> None:
    doc = ReleaseInfoDocument(
        album="—",
        album_artist="Studio Artist",
        artist="Studio Artist",
        release_type="album",
        status="Final",
        date="2026-11-25",
        original_date="2026-11-25",
        year="2026",
        genre=["Ambient"],
        label="—",
        distribution=DistributionInfo(
            credits=(
                "## Identidad\n"
                "- **Album:** FAKE INJECTION\n"
                "## Reporte de Verificación\n"
                "Line with ```backticks``` inside\n"
                "—"
            )
        ),
        tracks=[
            ReleaseInfoTrack(
                disc_number=1,
                track_number=1,
                total_tracks=1,
                title="—",
                artist="Studio Artist",
                genre=["Ambient"],
                duration_seconds=10.0,
                format="flac",
                filename="01 - Track.flac",
                verified=True,
                has_cover=False,
            )
        ],
        all_verified=True,
    )

    md = render_release_info_md(doc)
    parsed = parse_release_info_md(md)
    assert parsed == doc


def test_api_rejects_empty_output_dir_invalid_cover_and_external_origin(
    tmp_path: Path,
) -> None:
    app = create_app(presets_dir=tmp_path / "presets")
    client = TestClient(app)

    # Origin externo bloqueado con 403
    ext_res = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert ext_res.status_code == 403

    # Origin localhost permitido
    ok_res = client.get("/api/health", headers={"Origin": "http://127.0.0.1:8765"})
    assert ok_res.status_code == 200

    wav = create_silent_wav(tmp_path / "src" / "a.wav", duration_seconds=0.5)

    # output_dir="" -> HTTP 400 (no resuelve a `.` ni `/`)
    empty_out_payload = {
        "album": "Test",
        "album_artist": "Studio Artist",
        "date": "2026-11-25",
        "genre": ["Ambient"],
        "release_type": "album",
        "output_dir": "   ",
        "tracks": [{"title": "A", "track_number": 1, "disc_number": 1}],
    }
    res_empty = client.post(
        "/api/process",
        data={"payload": json.dumps(empty_out_payload)},
        files=[("audio_files", ("a.wav", wav.read_bytes(), "audio/wav"))],
    )
    assert res_empty.status_code == 400

    # Portada inválida -> HTTP 400 legible y no crea carpeta destino
    export_dir = tmp_path / "bad_cover_exports"
    bad_cover_payload = {**empty_out_payload, "output_dir": str(export_dir)}
    res_cover = client.post(
        "/api/process",
        data={"payload": json.dumps(bad_cover_payload)},
        files=[
            ("audio_files", ("a.wav", wav.read_bytes(), "audio/wav")),
            ("cover_file", ("broken.jpg", b"not-a-valid-image", "image/jpeg")),
        ],
    )
    assert res_cover.status_code == 400
    assert not (export_dir / "Studio Artist").exists()


def test_open_folder_passes_double_dash_and_pick_folder_passes_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        captured_cmds.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{tmp_path}\n", stderr="")

    monkeypatch.setattr(web_app_module.sys, "platform", "darwin")
    monkeypatch.setattr(web_app_module.subprocess, "run", _fake_run)

    pick = web_app_module._run_macos_pick_folder(initial_dir=str(tmp_path))
    assert pick["selected"] is True
    assert captured_cmds[0][0] == "osascript"
    assert "--" in captured_cmds[0]
    assert str(tmp_path) == captured_cmds[0][-1]

    web_app_module._open_in_file_manager(tmp_path)
    assert captured_cmds[1] == ["open", "--", str(tmp_path)]


def test_local_server_double_stop_idempotent_and_orphan_temp_cleanup(
    tmp_path: Path,
) -> None:
    orphan_dir = tmp_path / "orphan_album"
    orphan_dir.mkdir()
    t1 = orphan_dir / ".tmp_meta_abc.flac"
    t2 = orphan_dir / ".tmp_cover_xyz.jpg"
    keep = orphan_dir / "01 - Good.flac"
    t1.write_bytes(b"tmp")
    t2.write_bytes(b"tmp")
    keep.write_bytes(b"keep")

    removed = cleanup_orphan_temps([orphan_dir])
    assert removed == 2
    assert not t1.exists()
    assert not t2.exists()
    assert keep.exists()

    # Doble stop() en LocalUvicornServer no debe fallar
    app = create_app(presets_dir=tmp_path / "presets")
    server = LocalUvicornServer(app)
    server.stop()
    server.stop()
