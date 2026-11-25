from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from meta_studio.core.sanitizer import (
    build_album_dir,
    build_track_filename,
    sanitize_name,
)


def test_sanitize_name_normalizes_nfd_to_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "Canción de Niño: Época")
    result = sanitize_name(nfd)
    assert unicodedata.is_normalized("NFC", result)
    assert result == "Canción de Niño - Época"


def test_sanitize_name_replaces_colon_and_removes_forbidden_chars() -> None:
    raw = '  Act I:  "Sun/Moon*Back\\Slash?<>|"   ...  '
    assert sanitize_name(raw) == "Act I - SunMoonBackSlash"


def test_sanitize_name_preserves_user_casing() -> None:
    raw = "iPhone Lo-Fi: DJ set (VIP)"
    assert sanitize_name(raw) == "iPhone Lo-Fi - DJ set (VIP)"


def test_sanitize_name_rejects_empty_result() -> None:
    with pytest.raises(ValueError):
        sanitize_name('///???***...')


def test_build_album_dir_status_variants(tmp_path: Path) -> None:
    final_dir = build_album_dir(
        tmp_path, "Hakon: Live", "2026", "Part 1: Dawn...", status="Final"
    )
    demo_dir = build_album_dir(
        tmp_path, "Hakon", "2026", "Sketches", status="Demo"
    )
    wip_dir = build_album_dir(
        tmp_path, "Hakon", "2026", "Sketches", status="WIP"
    )

    assert final_dir == tmp_path / "Hakon - Live" / "[2026] Part 1 - Dawn"
    assert demo_dir == tmp_path / "Hakon" / "[2026] Sketches [Demo]"
    assert wip_dir == tmp_path / "Hakon" / "[2026] Sketches [WIP]"


def test_build_track_filename_single_and_multidisc() -> None:
    single = build_track_filename(3, "Act II: Night?", "FLAC")
    multi = build_track_filename(
        4, "Bonus: Mix...", ".mp3", disc_number=2, total_discs=2
    )

    assert single == "03 - Act II - Night.flac"
    assert multi == "2-04 - Bonus - Mix.mp3"
    assert unicodedata.is_normalized("NFC", single)
    assert unicodedata.is_normalized("NFC", multi)
