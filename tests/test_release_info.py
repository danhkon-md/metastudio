from __future__ import annotations

from pathlib import Path

from meta_studio.core.models import DistributionInfo, ReleaseInput, TrackInput
from meta_studio.core.pipeline import process_release
from meta_studio.services.release_info import (
    ReleaseInfoDocument,
    ReleaseInfoTrack,
    format_duration,
    parse_duration,
    parse_release_info_json,
    parse_release_info_md,
    read_release_info,
    render_release_info_json,
    render_release_info_md,
    write_release_info,
)
from tests.conftest import create_silent_wav


def _sample_full_document() -> ReleaseInfoDocument:
    return ReleaseInfoDocument(
        album="Glass & Tape: Vol. 1",
        album_artist="Studio Artist",
        artist="Studio Artist",
        release_type="album",
        status="Final",
        date="2026-11-25",
        original_date="2025-12-01",
        year="2026",
        genre=["Ambient", "Tape Loops", "Field Recordings"],
        label="Independent Label",
        catalog_number="CAT-014",
        copyright="℗ 2026 Independent Label",
        distribution=DistributionInfo(
            primary_genre="Electronic",
            secondary_genre="Ambient",
            language="Instrumental",
            instrumental=True,
            explicit=False,
            upc="198001234567",
            credits=(
                "Written, recorded and mixed by Studio Artist.\n"
                "Mastered at Studio A | Mexico City.\n"
                "Cover photography by Guest Photographer."
            ),
        ),
        cover_file="cover.jpg",
        all_verified=True,
        tracks=[
            ReleaseInfoTrack(
                disc_number=1,
                track_number=1,
                total_tracks=2,
                title="Morning | Static",
                artist="Studio Artist",
                genre=["Ambient", "Tape Loops", "Field Recordings"],
                duration_seconds=184.5,
                format="flac",
                filename="1-01 - Morning Static.flac",
                isrc="USXX02600001",
                verified=True,
                has_cover=True,
                id3_version=None,
            ),
            ReleaseInfoTrack(
                disc_number=1,
                track_number=2,
                total_tracks=2,
                title="Lo-Fi Rain",
                artist="Studio Artist & Guest",
                genre=["Lo-Fi", "Drone"],
                duration_seconds=240.0,
                format="mp3",
                filename="1-02 - Lo-Fi Rain.mp3",
                isrc="USXX02600002",
                verified=True,
                has_cover=True,
                id3_version="2.4.0",
            ),
            ReleaseInfoTrack(
                disc_number=2,
                track_number=1,
                total_tracks=1,
                title="Bonus Sketch",
                artist="Studio Artist",
                genre=["Ambient", "Tape Loops", "Field Recordings"],
                duration_seconds=65.25,
                format="wav",
                filename="2-01 - Bonus Sketch.wav",
                isrc=None,
                verified=True,
                has_cover=False,
                id3_version="2.4.0",
            ),
        ],
    )


def test_duration_format_and_parse_roundtrip() -> None:
    assert format_duration(185.0) == "03:05"
    assert parse_duration("03:05") == 185.0
    assert format_duration(0.5) == "00:00.50"
    assert parse_duration("00:00.50") == 0.5
    assert parse_duration("01:02:03.25") == 3723.25
    assert format_duration(None) == "—"
    assert parse_duration("—") is None


def test_release_info_json_and_markdown_lossless_roundtrip(tmp_path: Path) -> None:
    doc = _sample_full_document()

    json_text = render_release_info_json(doc)
    md_text = render_release_info_md(doc)

    # Verificar secciones clave para copiar/pegar en Bandcamp/DistroKid
    for heading in [
        "## Identidad",
        "## Distribución",
        "## Tracklist",
        "## Créditos y Notas",
        "## Reporte de Verificación",
    ]:
        assert heading in md_text

    assert parse_release_info_json(json_text) == doc
    assert parse_release_info_md(md_text) == doc

    json_path, md_path = write_release_info(tmp_path / "album", doc)
    assert json_path.name == "release-info.json"
    assert md_path.name == "release-info.md"
    assert read_release_info(json_path) == doc
    assert read_release_info(md_path) == doc


def test_release_info_minimal_document_roundtrip() -> None:
    minimal = ReleaseInfoDocument(
        album="Sketches",
        album_artist="Studio Artist",
        artist="Studio Artist",
        release_type="demo",
        status="Demo",
        date="2026-11-25",
        original_date="2026-11-25",
        year="2026",
        genre=["Drone"],
        tracks=[
            ReleaseInfoTrack(
                disc_number=1,
                track_number=1,
                total_tracks=1,
                title="idea 1",
                artist="Studio Artist",
                genre=["Drone"],
                duration_seconds=12.0,
                format="flac",
                filename="01 - idea 1.flac",
                verified=True,
                has_cover=False,
            )
        ],
        all_verified=True,
    )

    assert parse_release_info_md(render_release_info_md(minimal)) == minimal
    assert parse_release_info_json(render_release_info_json(minimal)) == minimal


def test_pipeline_generates_cover_and_reimportable_release_info(
    tmp_path: Path, sample_cover_png: Path
) -> None:
    w1 = create_silent_wav(tmp_path / "in" / "01_opening_24bit_master.wav", duration_seconds=0.5)
    w2 = create_silent_wav(tmp_path / "in" / "02_closing_24bit_master.wav", duration_seconds=0.5)

    release = ReleaseInput(
        album="Cassette Tape",
        album_artist="Studio Artist",
        date="2026-11-25",
        genre=["Ambient", "Tape Loops"],
        release_type="album",
        status="Final",
        label="Independent Label",
        catalog_number="CAT-007",
        copyright="℗ 2026 Independent Label",
        distribution=DistributionInfo(
            primary_genre="Electronic",
            secondary_genre="Ambient",
            language="Spanish",
            instrumental=True,
            explicit=False,
            upc="123456789012",
            credits="Produced by Studio Artist.\nRecorded on 4-track cassette.",
        ),
        output_dir=tmp_path / "out",
        cover_path=sample_cover_png,
        wav_conversion="flac",
        tracks=[
            TrackInput(
                source_path=w1,
                title="opening",
                track_number=1,
                isrc="USXX02600011",
            ),
            TrackInput(
                source_path=w2,
                title="closing",
                track_number=2,
                isrc="USXX02600012",
            ),
        ],
    )

    result = process_release(release)

    assert result.all_verified is True
    assert result.cover_path == result.album_dir / "cover.jpg"
    assert result.release_info_json_path == result.album_dir / "release-info.json"
    assert result.release_info_md_path == result.album_dir / "release-info.md"
    assert result.cover_path.is_file()
    assert result.release_info_json_path.is_file()
    assert result.release_info_md_path.is_file()

    from_json = read_release_info(result.release_info_json_path)
    from_md = read_release_info(result.release_info_md_path)

    assert from_json == from_md
    assert from_json.label == "Independent Label"
    assert from_json.catalog_number == "CAT-007"
    assert from_json.distribution.upc == "123456789012"
    assert from_json.distribution.instrumental is True
    assert [t.isrc for t in from_json.tracks] == ["USXX02600011", "USXX02600012"]
    assert from_json.all_verified is True
