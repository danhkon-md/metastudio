from __future__ import annotations

import unicodedata

import pytest

from meta_studio.services.cleaner import clean_track_title, clean_track_titles


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Preserva mayúsculas/minúsculas exactas (sin Title Case automático)
        ("01_my_iPhone_track_24bit_48kHz_final_v2_master.wav", "my iPhone track"),
        ("1-02 - Lo-Fi_Sketch_unmastered_44k_rough.flac", "Lo-Fi Sketch"),
        ("03. DJ_NASA_demo_16bit_export.mp3", "DJ NASA"),
        ("04 - all lowercase - mix_v3.wav", "all lowercase"),
        ("05_MiXeD_CaSe_TRACK_FINAL_MASTER.flac", "MiXeD CaSe TRACK"),
        # Sufijos en paréntesis y corchetes técnicos
        ("06_Echoes (final master).wav", "Echoes"),
        ("07_Tape_Loops [24bit 48kHz].flac", "Tape Loops"),
        ("08 - Night Walk (rough mix) - v2.wav", "Night Walk"),
        # Paréntesis NO técnicos se conservan
        ("09 - Keep (Live at Berlin)_master_24bit.wav", "Keep (Live at Berlin)"),
        ("10_Song (feat. Guest Artist)_final_export.flac", "Song (feat. Guest Artist)"),
        # Años de 4 dígitos no se eliminan como prefijo de pista
        ("1984_master_48kHz.wav", "1984"),
        ("2026_demo_16bit.wav", "2026"),
        # Números decimales en títulos sin extensión de audio
        ("02 - Track 2.5_rough", "Track 2.5"),
        # Rutas con directorios previos: solo toma el nombre de archivo
        ("/Music/Bounces/01_sun_rise_unmastered_v1.wav", "sun rise"),
    ],
)
def test_clean_track_title_preserves_casing_and_strips_tech_junk(
    raw: str, expected: str
) -> None:
    assert clean_track_title(raw) == expected


def test_clean_track_title_normalizes_nfd_to_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "01_Canción_de_Otoño_48kHz_master.wav")
    cleaned = clean_track_title(nfd)
    assert unicodedata.is_normalized("NFC", cleaned)
    assert cleaned == "Canción de Otoño"


def test_clean_track_titles_batch() -> None:
    result = clean_track_titles(
        [
            "01_intro_demo_16bit.wav",
            "02_iPod_noise_24bit_44k_export.wav",
        ]
    )
    assert result == ["intro", "iPod noise"]
