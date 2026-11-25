from __future__ import annotations

import hashlib
import subprocess
import wave
from pathlib import Path

import pytest
from PIL import Image

from meta_studio.core.converter import resolve_ffmpeg_binary


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_silent_wav(
    path: Path,
    *,
    duration_seconds: float = 0.5,
    sample_rate: int = 44100,
    channels: int = 2,
) -> Path:
    """Genera un archivo WAV de silencio al vuelo sin depender de archivos externos."""
    num_frames = int(duration_seconds * sample_rate)
    silence = b"\x00\x00" * channels * num_frames
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(silence)
    return path


def create_silent_flac(path: Path, *, duration_seconds: float = 0.5) -> Path:
    """Genera un archivo FLAC de silencio al vuelo vía ffmpeg en un directorio temporal."""
    tmp_wav = path.with_suffix(".gen.wav")
    create_silent_wav(tmp_wav, duration_seconds=duration_seconds)
    try:
        ffmpeg_bin = resolve_ffmpeg_binary() or "ffmpeg"
        subprocess.run(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(tmp_wav),
                "-map_metadata",
                "-1",
                "-c:a",
                "flac",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        if tmp_wav.exists():
            tmp_wav.unlink()
    return path


def create_silent_mp3(path: Path, *, duration_seconds: float = 0.5) -> Path:
    """Genera un archivo MP3 de silencio al vuelo vía ffmpeg en un directorio temporal."""
    tmp_wav = path.with_suffix(".gen.wav")
    create_silent_wav(tmp_wav, duration_seconds=duration_seconds)
    try:
        ffmpeg_bin = resolve_ffmpeg_binary() or "ffmpeg"
        subprocess.run(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(tmp_wav),
                "-map_metadata",
                "-1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        if tmp_wav.exists():
            tmp_wav.unlink()
    return path


def create_test_cover(
    path: Path,
    *,
    size: tuple[int, int] = (1800, 1800),
    mode: str = "RGBA",
    color: tuple[int, ...] = (40, 120, 200, 220),
    fmt: str = "PNG",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new(mode, size, color)
    img.save(path, format=fmt)
    return path


@pytest.fixture
def sample_wav(tmp_path: Path) -> Path:
    return create_silent_wav(tmp_path / "src" / "01_demo_master.wav", duration_seconds=0.5)


@pytest.fixture
def sample_flac(tmp_path: Path) -> Path:
    return create_silent_flac(tmp_path / "src" / "01_track.flac", duration_seconds=0.5)


@pytest.fixture
def sample_mp3(tmp_path: Path) -> Path:
    return create_silent_mp3(tmp_path / "src" / "01_track.mp3", duration_seconds=0.5)


@pytest.fixture
def sample_cover_png(tmp_path: Path) -> Path:
    return create_test_cover(tmp_path / "src" / "artwork.png", size=(2000, 1600))
