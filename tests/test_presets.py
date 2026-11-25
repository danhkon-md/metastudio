from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from meta_studio.services.presets import (
    GenresCatalog,
    PresetStore,
    ProfilePreset,
    ProfilesFile,
)


def test_repo_initial_profiles_match_expected_data() -> None:
    store = PresetStore()
    profiles = store.load_profiles()

    assert [p.id for p in profiles] == [
        "independent-artist",
        "demo-wip",
    ]

    indie = store.get_profile("independent-artist")
    assert indie.name == "Independent Artist"
    assert indie.artist == "Artist Name"
    assert indie.album_artist == "Artist Name"
    assert indie.label == "Independent"
    assert indie.copyright == "℗ {year} Artist Name"
    assert indie.render_copyright(2026) == "℗ 2026 Artist Name"
    assert indie.release_type == "album"
    assert indie.catalog_prefix == ""

    demo = store.get_profile("demo-wip")
    assert demo.name == "Demo / WIP"
    assert demo.label == ""
    assert demo.copyright == ""
    assert demo.render_copyright(2026) == ""
    assert demo.release_type == "demo"
    assert demo.catalog_prefix == ""


def test_repo_initial_genres_match_expected_data() -> None:
    store = PresetStore()
    genres = store.load_genres()

    assert genres.distributor_primary == [
        "Electronic",
        "Ambient",
        "Experimental",
        "Alternative",
        "Indie",
        "Classical",
        "Hip-Hop/Rap",
        "Folk",
        "Rock",
        "Soundtrack",
    ]
    assert genres.tags == [
        "Ambient",
        "Drone",
        "Plunderphonics",
        "Sound Collage",
        "Experimental",
        "Electronic",
        "Modern Classical",
        "Electroacoustic",
        "Indie-tronica",
        "Electroclash",
        "Lo-Fi",
        "Tape Loops",
        "Field Recordings",
        "Folk",
        "Post-Rock",
        "Trap",
        "Plugg",
        "Noise",
        "Dark Ambient",
        "Acoustic",
    ]


def test_preset_store_profile_crud_and_atomic_persistence(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    assert store.load_profiles() == []

    p1 = ProfilePreset(
        id="project-one",
        name="Project One",
        artist="Artist One",
        album_artist="Artist One",
        label="Label One",
        copyright="℗ {year} Label One",
        release_type="ep",
        catalog_prefix="L1-",
    )
    store.upsert_profile(p1)
    assert len(store.load_profiles()) == 1
    assert store.get_profile("project-one").label == "Label One"

    # Update existente
    p1_updated = p1.model_copy(update={"label": "Label Updated"})
    store.upsert_profile(p1_updated)
    assert len(store.load_profiles()) == 1
    assert store.get_profile("project-one").label == "Label Updated"

    # Sin archivos temporales residuales
    assert list(tmp_path.iterdir()) == [store.profiles_path]

    # Delete
    store.delete_profile("project-one")
    assert store.load_profiles() == []
    with pytest.raises(KeyError):
        store.get_profile("project-one")
    with pytest.raises(KeyError):
        store.delete_profile("project-one")


def test_preset_store_genres_dedupe_add_and_remove(tmp_path: Path) -> None:
    store = PresetStore(tmp_path)
    saved = store.save_genres(
        GenresCatalog(
            distributor_primary=["Electronic", "Ambient", "Electronic"],
            tags=["Drone", "Lo-Fi", "Drone"],
        )
    )
    assert saved.distributor_primary == ["Electronic", "Ambient"]
    assert saved.tags == ["Drone", "Lo-Fi"]

    after_add = store.add_genre_tag("Tape Loops")
    assert after_add.tags == ["Drone", "Lo-Fi", "Tape Loops"]

    # Agregar duplicado no duplica
    after_dup = store.add_genre_tag("Lo-Fi")
    assert after_dup.tags == ["Drone", "Lo-Fi", "Tape Loops"]

    after_remove = store.remove_genre_tag("Drone")
    assert after_remove.tags == ["Lo-Fi", "Tape Loops"]


def test_presets_validation_errors() -> None:
    with pytest.raises(ValidationError):
        ProfilePreset(
            id="Bad ID!",
            name="Bad",
            artist="A",
            album_artist="A",
        )

    p = ProfilePreset(id="dup", name="One", artist="A", album_artist="A")
    with pytest.raises(ValidationError):
        ProfilesFile(profiles=[p, p])

    with pytest.raises(ValidationError):
        GenresCatalog(distributor_primary=["Electronic"], tags=["   "])
