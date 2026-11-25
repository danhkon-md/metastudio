from __future__ import annotations

import os
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from meta_studio.core.fs import finalize_temp_file

MAX_COVER_DIMENSION = 1400
# Límite seguro anti decompression-bomb (~6300x6300 px)
Image.MAX_IMAGE_PIXELS = 40_000_000


def process_cover(
    source: Path | bytes,
    *,
    max_dimension: int = MAX_COVER_DIMENSION,
    quality: int = 92,
) -> bytes:
    """Convierte una imagen de portada a JPEG RGB con lado máximo <= max_dimension."""
    if max_dimension < 1:
        raise ValueError("max_dimension debe ser >= 1")

    raw_input: Path | BytesIO = BytesIO(source) if isinstance(source, bytes) else Path(source)
    with Image.open(raw_input) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[3])
            rgb = background
        elif img.mode != "RGB":
            rgb = img.convert("RGB")
        else:
            rgb = img.copy()

        if rgb.width > max_dimension or rgb.height > max_dimension:
            rgb.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

        buffer = BytesIO()
        rgb.save(buffer, format="JPEG", quality=quality, optimize=True)
        return buffer.getvalue()


def get_jpeg_dimensions(cover_bytes: bytes) -> tuple[int, int]:
    """Obtiene (width, height) de bytes JPEG ya procesados."""
    with Image.open(BytesIO(cover_bytes)) as img:
        return img.size


def save_cover_atomic(cover_bytes: bytes, dest_path: Path) -> Path:
    """Guarda `cover.jpg` usando escritura atómica en el mismo directorio destino."""
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=dest_path.parent, prefix=".tmp_cover_", suffix=".jpg"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(cover_bytes)
            handle.flush()
        return finalize_temp_file(tmp_path, dest_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
