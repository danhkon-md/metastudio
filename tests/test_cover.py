from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from meta_studio.core.cover import (
    MAX_COVER_DIMENSION,
    get_jpeg_dimensions,
    process_cover,
    save_cover_atomic,
)
from tests.conftest import create_test_cover


def test_process_cover_resizes_large_rgba_png_to_max_1400_jpeg(tmp_path: Path) -> None:
    src = create_test_cover(
        tmp_path / "big.png", size=(2800, 1400), mode="RGBA", fmt="PNG"
    )
    jpeg_bytes = process_cover(src)

    with Image.open(BytesIO(jpeg_bytes)) as out:
        assert out.format == "JPEG"
        assert out.mode == "RGB"
        assert out.size == (MAX_COVER_DIMENSION, 700)

    assert get_jpeg_dimensions(jpeg_bytes) == (1400, 700)


def test_process_cover_does_not_upscale_small_image(tmp_path: Path) -> None:
    src = create_test_cover(
        tmp_path / "small.jpg", size=(600, 600), mode="RGB", color=(10, 20, 30), fmt="JPEG"
    )
    jpeg_bytes = process_cover(src)
    assert get_jpeg_dimensions(jpeg_bytes) == (600, 600)


def test_save_cover_atomic_writes_cleanly_without_temp_files(tmp_path: Path) -> None:
    src = create_test_cover(tmp_path / "art.png", size=(1000, 1000))
    jpeg_bytes = process_cover(src)

    dest = tmp_path / "out" / "cover.jpg"
    saved = save_cover_atomic(jpeg_bytes, dest)

    assert saved == dest
    assert dest.read_bytes() == jpeg_bytes
    assert list(dest.parent.iterdir()) == [dest]
