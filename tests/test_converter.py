from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.flac import FLAC
from mutagen.id3 import COMM, TIT2, Encoding
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

from meta_studio.core.converter import (
    ConversionError,
    build_ffmpeg_command,
    convert_wav,
    is_ffmpeg_available,
)
from tests.conftest import sha256_file


def test_ffmpeg_is_available() -> None:
    assert is_ffmpeg_available() is True


@pytest.mark.parametrize("target_format", ["flac", "mp3_320"])
def test_build_ffmpeg_command_always_strips_daw_metadata_and_video(
    tmp_path: Path, target_format: str
) -> None:
    cmd = build_ffmpeg_command(
        tmp_path / "in.wav", target_format, tmp_path / "out.tmp"  # type: ignore[arg-type]
    )
    idx = cmd.index("-map_metadata")
    assert cmd[idx + 1] == "-1"
    assert "-vn" in cmd
    if target_format == "mp3_320":
        assert "320k" in cmd
        assert "libmp3lame" in cmd
    else:
        assert "flac" in cmd


def test_convert_wav_to_flac_and_mp3_strips_prior_daw_metadata_and_keeps_source(
    sample_wav: Path, tmp_path: Path
) -> None:
    # Inyectar metadata basura previa de DAW en el WAV origen
    wav_audio = WAVE(sample_wav)
    wav_audio.add_tags()
    assert wav_audio.tags is not None
    wav_audio.tags.add(TIT2(encoding=Encoding.UTF8, text=["DAW JUNK TITLE"]))
    wav_audio.tags.add(
        COMM(encoding=Encoding.UTF8, lang="eng", desc="", text=["Ableton export junk"])
    )
    wav_audio.save(v2_version=3)

    source_hash_before = sha256_file(sample_wav)

    flac_out = convert_wav(sample_wav, "flac", tmp_path / "converted.flac")
    mp3_out = convert_wav(sample_wav, "mp3_320", tmp_path / "converted.mp3")

    # Copy-First: el WAV origen sigue idéntico bit por bit
    assert sha256_file(sample_wav) == source_hash_before

    flac_audio = FLAC(flac_out)
    assert flac_audio.info.length > 0.4
    # Ningún tag previo del DAW debe sobrevivir a -map_metadata -1 -vn
    assert "title" not in flac_audio
    assert "comment" not in flac_audio
    assert flac_audio.pictures == []

    mp3_audio = MP3(mp3_out)
    assert mp3_audio.info.length > 0.4
    assert mp3_audio.info.bitrate >= 300_000
    if mp3_audio.tags is not None:
        assert mp3_audio.tags.getall("TIT2") == []
        assert mp3_audio.tags.getall("COMM") == []
        assert mp3_audio.tags.getall("APIC") == []


def test_convert_wav_rejects_non_wav_source(sample_flac: Path, tmp_path: Path) -> None:
    with pytest.raises(ConversionError):
        convert_wav(sample_flac, "mp3_320", tmp_path / "out.mp3")
