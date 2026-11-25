from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, COMM, TCON, TIT2, Encoding
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

from meta_studio.core.cover import process_cover
from meta_studio.core.models import ReleaseInput, TrackInput, TrackMetadata
from meta_studio.core.pipeline import process_release
from meta_studio.core.tagger import TaggingError, write_tagged_copy
from meta_studio.core.verifier import (
    VerificationError,
    read_audio_duration,
    verify_audio_file,
)
from tests.conftest import create_silent_wav, create_test_cover, sha256_file


def _base_metadata(*, with_optional: bool = True) -> TrackMetadata:
    return TrackMetadata(
        title="Act I: Midnight Signal",
        artist="Hakon",
        album="Neon Horizons",
        album_artist="Hakon",
        date="2026-11-25",
        original_date="2025-10-01",
        track_number=1,
        total_tracks=2,
        disc_number=1,
        total_discs=1,
        genre=["Synthwave", "Darkwave", "Ambient"],
        release_type="Album",
        label="Midnight Records" if with_optional else None,
        catalog_number="MR-001" if with_optional else None,
        copyright="© 2026 Midnight Records" if with_optional else None,
    )


def _inject_junk_flac(path: Path, old_cover: bytes) -> None:
    audio = FLAC(path)
    audio["TITLE"] = ["OLD JUNK"]
    audio["COMMENT"] = ["DAW junk comment"]
    audio["LABEL"] = ["OLD LABEL TO BE REMOVED"]
    pic = Picture()
    pic.type = 3
    pic.mime = "image/jpeg"
    pic.data = old_cover
    audio.add_picture(pic)
    audio.save()


def _inject_junk_mp3_v23(path: Path, old_cover: bytes) -> None:
    audio = MP3(path)
    if audio.tags is None:
        audio.add_tags()
    assert audio.tags is not None
    audio.tags.add(TIT2(encoding=Encoding.UTF16, text=["OLD JUNK"]))
    audio.tags.add(COMM(encoding=Encoding.UTF16, lang="eng", desc="", text=["DAW junk"]))
    audio.tags.add(TCON(encoding=Encoding.UTF16, text=["Rock, Pop"]))
    audio.tags.add(
        APIC(encoding=Encoding.UTF16, mime="image/jpeg", type=3, desc="Old", data=old_cover)
    )
    # Guardar deliberadamente en ID3v2.3 + ID3v1 para verificar limpieza y upgrade a v2.4
    audio.tags.save(path, v1=2, v2_version=3)


def _inject_junk_wav_v23(path: Path, old_cover: bytes) -> None:
    audio = WAVE(path)
    audio.add_tags()
    assert audio.tags is not None
    audio.tags.add(TIT2(encoding=Encoding.UTF16, text=["OLD WAV JUNK"]))
    audio.tags.add(COMM(encoding=Encoding.UTF16, lang="eng", desc="", text=["DAW wav junk"]))
    audio.tags.add(
        APIC(encoding=Encoding.UTF16, mime="image/jpeg", type=3, desc="Old", data=old_cover)
    )
    audio.save(v2_version=3)


def test_flac_copy_first_strips_junk_writes_multigenre_and_verifies(
    sample_flac: Path, sample_cover_png: Path, tmp_path: Path
) -> None:
    old_cover = process_cover(
        create_test_cover(tmp_path / "old.jpg", size=(200, 200), mode="RGB", color=(255, 0, 0), fmt="JPEG")
    )
    new_cover = process_cover(sample_cover_png)
    _inject_junk_flac(sample_flac, old_cover)

    source_hash = sha256_file(sample_flac)
    source_duration = read_audio_duration(sample_flac)
    meta = _base_metadata(with_optional=True)

    dest = tmp_path / "out" / "01 - Act I - Midnight Signal.flac"
    written, fmt, converted = write_tagged_copy(
        sample_flac, dest, meta, cover_bytes=new_cover
    )

    assert written == dest
    assert fmt == "flac"
    assert converted is False
    # Copy-First intacto
    assert sha256_file(sample_flac) == source_hash
    # Sin archivos temporales residuales
    assert list(dest.parent.iterdir()) == [dest]

    # Verificar multi-género real en Vorbis Comments y eliminación de basura previa
    reopened = FLAC(dest)
    assert reopened["GENRE"] == ["Synthwave", "Darkwave", "Ambient"]
    assert "COMMENT" not in reopened
    assert len(reopened.pictures) == 1
    assert reopened.pictures[0].data == new_cover

    result = verify_audio_file(
        dest,
        meta,
        expected_cover_bytes=new_cover,
        expected_duration_seconds=source_duration,
    )
    assert result.passed is True
    assert result.mismatches == []
    assert result.cover_matches is True


def test_mp3_forces_id3v24_strips_v23_junk_and_writes_multivalue_tcon(
    sample_mp3: Path, sample_cover_png: Path, tmp_path: Path
) -> None:
    old_cover = process_cover(
        create_test_cover(tmp_path / "old.jpg", size=(200, 200), mode="RGB", color=(255, 0, 0), fmt="JPEG")
    )
    new_cover = process_cover(sample_cover_png)
    _inject_junk_mp3_v23(sample_mp3, old_cover)

    source_hash = sha256_file(sample_mp3)
    source_duration = read_audio_duration(sample_mp3)
    meta = _base_metadata(with_optional=False)

    dest = tmp_path / "out" / "01 - Act I - Midnight Signal.mp3"
    written, fmt, converted = write_tagged_copy(
        sample_mp3, dest, meta, cover_bytes=new_cover
    )

    assert written == dest
    assert fmt == "mp3"
    assert converted is False
    assert sha256_file(sample_mp3) == source_hash

    reopened = MP3(dest)
    assert reopened.tags is not None
    assert reopened.tags.version == (2, 4, 0)
    assert reopened.tags.getall("COMM") == []
    assert reopened.tags.getall("TSSE") == []
    assert reopened.tags.getall("TPUB") == []
    assert reopened.tags.getall("TCOP") == []
    tcon = reopened.tags.getall("TCON")[0]
    assert tcon.text == ["Synthwave", "Darkwave", "Ambient"]

    result = verify_audio_file(
        dest,
        meta,
        expected_cover_bytes=new_cover,
        expected_duration_seconds=source_duration,
    )
    assert result.passed is True
    assert result.id3_version == (2, 4, 0)
    assert result.mismatches == []


def test_wav_keep_forces_id3v24_strips_prior_tags_and_verifies(
    sample_wav: Path, tmp_path: Path
) -> None:
    old_cover = process_cover(
        create_test_cover(tmp_path / "old.jpg", size=(100, 100), mode="RGB", color=(0, 255, 0), fmt="JPEG")
    )
    _inject_junk_wav_v23(sample_wav, old_cover)

    source_hash = sha256_file(sample_wav)
    source_duration = read_audio_duration(sample_wav)
    meta = _base_metadata(with_optional=True)

    dest = tmp_path / "out" / "01 - Act I - Midnight Signal.wav"
    written, fmt, converted = write_tagged_copy(
        sample_wav, dest, meta, cover_bytes=None, wav_conversion="keep"
    )

    assert written == dest
    assert fmt == "wav"
    assert converted is False
    assert sha256_file(sample_wav) == source_hash

    reopened = WAVE(dest)
    assert reopened.tags is not None
    assert reopened.tags.version == (2, 4, 0)
    assert reopened.tags.getall("COMM") == []
    assert reopened.tags.getall("APIC") == []

    result = verify_audio_file(
        dest,
        meta,
        expected_cover_bytes=None,
        expected_duration_seconds=source_duration,
    )
    assert result.passed is True
    assert result.has_cover is False
    assert result.cover_matches is True


def test_write_tagged_copy_blocks_in_place_overwrite(sample_flac: Path) -> None:
    meta = _base_metadata()
    with pytest.raises(TaggingError, match="Copy-First"):
        write_tagged_copy(sample_flac, sample_flac, meta)


def test_atomic_write_cleans_temp_file_on_error(
    sample_flac: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args, **_kwargs) -> None:
        raise RuntimeError("simulated tagging crash")

    monkeypatch.setattr("meta_studio.core.tagger._tag_flac_inplace", _boom)

    dest_dir = tmp_path / "atomic_out"
    dest = dest_dir / "01 - Crash.flac"
    with pytest.raises(RuntimeError, match="simulated tagging crash"):
        write_tagged_copy(sample_flac, dest, _base_metadata())

    assert not dest.exists()
    assert list(dest_dir.iterdir()) == []


def test_read_back_verifier_detects_tampered_tag_and_missing_cover(
    sample_mp3: Path, sample_cover_png: Path, tmp_path: Path
) -> None:
    cover_bytes = process_cover(sample_cover_png)
    meta = _base_metadata(with_optional=True)
    dest = tmp_path / "out" / "01 - Track.mp3"
    write_tagged_copy(sample_mp3, dest, meta, cover_bytes=cover_bytes)

    # Corromper deliberadamente el título, borrar portada y bajar a ID3v2.3
    tampered = MP3(dest)
    assert tampered.tags is not None
    tampered.tags.delall("TIT2")
    tampered.tags.add(TIT2(encoding=Encoding.UTF8, text=["TAMPERED"]))
    tampered.tags.delall("APIC")
    tampered.tags.save(dest, v1=0, v2_version=3)

    result = verify_audio_file(dest, meta, expected_cover_bytes=cover_bytes)
    assert result.passed is False
    failed_fields = {m.field for m in result.mismatches}
    assert {"TITLE", "COVER", "ID3_VERSION"}.issubset(failed_fields)

    with pytest.raises(VerificationError):
        raise VerificationError(result)


@pytest.mark.parametrize("wav_mode,expected_ext", [("flac", "flac"), ("mp3_320", "mp3")])
def test_pipeline_end_to_end_wav_conversion_multidisc_and_status(
    tmp_path: Path,
    sample_cover_png: Path,
    wav_mode: str,
    expected_ext: str,
) -> None:
    w1 = create_silent_wav(tmp_path / "in" / "track_a.wav", duration_seconds=0.5)
    w2 = create_silent_wav(tmp_path / "in" / "track_b.wav", duration_seconds=0.5)
    h1, h2 = sha256_file(w1), sha256_file(w2)

    release = ReleaseInput(
        album="Solaris: Part II",
        album_artist="Hakon",
        date="2026-11-25",
        genre=["Ambient", "Electronic"],
        release_type="Album",
        status="WIP",
        label="Independent",
        output_dir=tmp_path / "exports",
        cover_path=sample_cover_png,
        wav_conversion=wav_mode,  # type: ignore[arg-type]
        tracks=[
            TrackInput(source_path=w1, title="Act I: Dawn", track_number=1, disc_number=1),
            TrackInput(source_path=w2, title="Act II: Dusk?", track_number=1, disc_number=2),
        ],
    )

    result = process_release(release)

    assert result.all_verified is True
    assert result.album_dir == tmp_path / "exports" / "Hakon" / "[2026] Solaris - Part II [WIP]"
    assert result.cover_path == result.album_dir / "cover.jpg"
    assert result.cover_path.is_file()

    assert len(result.tracks) == 2
    assert result.tracks[0].output_path.name == f"1-01 - Act I - Dawn.{expected_ext}"
    assert result.tracks[1].output_path.name == f"2-01 - Act II - Dusk.{expected_ext}"
    assert all(t.converted is True for t in result.tracks)
    assert all(t.verification.passed is True for t in result.tracks)

    # Copy-First: archivos WAV de origen sin modificar
    assert sha256_file(w1) == h1
    assert sha256_file(w2) == h2
