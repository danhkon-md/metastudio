from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from meta_studio.core.models import ReleaseInput, TrackInput, TrackMetadata


def test_track_metadata_derives_year_and_defaults_original_date() -> None:
    meta = TrackMetadata(
        title="Midnight Signal",
        artist="Hakon",
        album="Neon Horizons",
        album_artist="Hakon",
        date="2026-11-25",
        track_number=1,
        total_tracks=8,
        disc_number=1,
        genre=["Synthwave", "Darkwave"],
        release_type="Album",
    )

    assert meta.year == "2026"
    assert meta.original_date == "2026-11-25"
    assert meta.label is None
    assert meta.catalog_number is None
    assert meta.copyright is None


def test_track_metadata_respects_explicit_original_date_and_optional_fields() -> None:
    meta = TrackMetadata(
        title="Echoes",
        artist="Hakon",
        album="Reissue",
        album_artist="Hakon",
        date="2026-11-25",
        original_date="2021-05-10",
        track_number=2,
        total_tracks=4,
        disc_number=1,
        genre=["Ambient", "Electronic", "Ambient"],
        release_type="EP",
        label="  Midnight Records  ",
        catalog_number="MR-042",
        copyright="© 2026 Midnight Records",
    )

    assert meta.year == "2026"
    assert meta.original_date == "2021-05-10"
    assert meta.genre == ["Ambient", "Electronic"]
    assert meta.label == "Midnight Records"
    assert meta.catalog_number == "MR-042"
    assert meta.copyright == "© 2026 Midnight Records"


def test_track_metadata_blank_optional_fields_become_none() -> None:
    meta = TrackMetadata(
        title="Minimal",
        artist="Hakon",
        album="Test",
        album_artist="Hakon",
        date="2026-01-15",
        original_date="   ",
        track_number=1,
        total_tracks=1,
        genre=["Techno"],
        release_type="Single",
        label="   ",
        catalog_number="",
        copyright="  ",
    )

    assert meta.original_date == "2026-01-15"
    assert meta.label is None
    assert meta.catalog_number is None
    assert meta.copyright is None


@pytest.mark.parametrize(
    "bad_kwargs",
    [
        {"date": "2026-1-5"},
        {"date": "2026-02-30"},
        {"date": "25-11-2026"},
        {"year": "2025"},
        {"genre": []},
        {"genre": ["   "]},
        {"track_number": 5, "total_tracks": 3},
        {"disc_number": 2, "total_discs": 1},
        {"title": "   "},
        {"release_type": ""},
    ],
)
def test_track_metadata_validation_errors(bad_kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {
        "title": "Track One",
        "artist": "Hakon",
        "album": "Album",
        "album_artist": "Hakon",
        "date": "2026-11-25",
        "track_number": 1,
        "total_tracks": 3,
        "disc_number": 1,
        "total_discs": 1,
        "genre": ["Electronic"],
        "release_type": "EP",
    }
    base.update(bad_kwargs)
    with pytest.raises(ValidationError):
        TrackMetadata(**base)


def test_release_input_resolves_single_and_multidisc_tracks(tmp_path: Path) -> None:
    release = ReleaseInput(
        album="Double Feature",
        album_artist="Hakon",
        date="2026-08-01",
        genre=["Ambient", "IDM"],
        release_type="Album",
        output_dir=tmp_path / "out",
        tracks=[
            TrackInput(source_path=tmp_path / "a.flac", title="Intro", track_number=1, disc_number=1),
            TrackInput(
                source_path=tmp_path / "b.flac",
                title="Collab",
                artist="Hakon feat. Guest",
                track_number=2,
                disc_number=1,
            ),
            TrackInput(
                source_path=tmp_path / "c.flac",
                title="Disc Two Opener",
                track_number=1,
                disc_number=2,
                genre=["Drone"],
            ),
        ],
    )

    resolved = release.resolve_tracks()
    assert len(resolved) == 3

    _, m1 = resolved[0]
    _, m2 = resolved[1]
    _, m3 = resolved[2]

    assert m1.year == "2026"
    assert m1.original_date == "2026-08-01"
    assert m1.artist == "Hakon"
    assert m1.total_tracks == 2
    assert m1.total_discs == 2

    assert m2.artist == "Hakon feat. Guest"
    assert m2.total_tracks == 2

    assert m3.disc_number == 2
    assert m3.total_tracks == 1
    assert m3.total_discs == 2
    assert m3.genre == ["Drone"]


def test_release_input_rejects_duplicate_track_position(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ReleaseInput(
            album="Dupes",
            album_artist="Hakon",
            date="2026-08-01",
            genre=["Ambient"],
            release_type="EP",
            output_dir=tmp_path / "out",
            tracks=[
                TrackInput(source_path=tmp_path / "a.flac", title="A", track_number=1),
                TrackInput(source_path=tmp_path / "b.flac", title="B", track_number=1),
            ],
        )
