# Reglas del Proyecto: Meta Studio

Herramienta web local minimalista para generar copias de archivos de audio con metadata estructurada, limpia y auto-verificada.

## 1. Stack Obligatorio
- Python 3.11+ gestionado exclusivamente con `uv`.
- Backend: `fastapi`, `uvicorn`, `python-multipart`.
- Core: `mutagen` (tags), `pydantic` v2 (validación), `Pillow` (portadas), `pytest` (tests).
- Conversión de audio: `ffmpeg` vía `subprocess` solo cuando se convierta de WAV a FLAC o MP3 320k.
- Frontend: HTML5, CSS moderno en **Light Mode minimalista** y Vanilla JS servido directo desde FastAPI (sin Node, sin bundlers).

## 2. Reglas de Seguridad e Integridad (Inviolables)
- **Copy-First:** NUNCA modificar, renombrar ni borrar archivos de origen. Siempre generar copias en la carpeta de salida.
- **Escritura atómica:** Procesar y taggear en un archivo temporal y mover al destino final solo cuando termine sin errores.
- **Read-Back Verification:** Después de guardar cada archivo, reabrirlo desde cero con `mutagen` y verificar que duración, portada incrustada y todos los tags coincidan exactamente con lo solicitado.
- **Sanitización macOS + Linux:** Normalizar siempre nombres de carpetas y archivos a Unicode NFC (`unicodedata.normalize("NFC", s)`). Reemplazar `:` por ` -`, eliminar `/ \ * ? " < > |`, colapsar espacios múltiples y quitar puntos/espacios al final.
- **Respetar mayúsculas/minúsculas del usuario:** Al limpiar nombres de archivo importados (quitar prefijos numéricos, guiones bajos y sufijos como `_master`, `_final`, `_mix`, `_v2`), NUNCA aplicar Title Case automático.

## 3. Estándares de Metadata y Salida
- Estructura de carpetas: `{output_dir}/{AlbumArtist}/[{Year}] {Album}/` (añadir ` [{Status}]` solo si es `Demo` o `WIP`).
- Archivos: `{track:02d} - {Title}.{ext}` (o `{disc}-{track:02d} - {Title}.{ext}` si hay más de 1 disco).
- Guardar siempre `cover.jpg` (máx. 1400x1400 JPEG), `release-info.md` y `release-info.json` en la carpeta del álbum.
- Géneros múltiples como lista real: múltiples etiquetas `GENRE` en FLAC (Vorbis Comments) y multi-valor real en MP3 (ID3v2.4), nunca concatenados con comas.
- Campos obligatorios: `TITLE`, `ARTIST`, `ALBUM`, `ALBUMARTIST`, `DATE` (YYYY-MM-DD), `ORIGINALDATE` (YYYY-MM-DD), `YEAR` (YYYY), `TRACKNUMBER`, `TOTALTRACKS`, `DISCNUMBER`, `GENRE`, `RELEASETYPE` (`TXXX:MusicBrainz Album Type` en ID3), `LABEL`, `CATALOGNUMBER`, `COPYRIGHT`.

## 4. Disciplina de Trabajo
- Trabajar estrictamente fase por fase según `PLAN.md`.
- Antes de dar por terminada cualquier fase, ejecutar `uv run pytest` y confirmar que todas las pruebas pasen en verde.
- En tests, generar audios de silencio cortos al vuelo en directorios temporales; nunca depender de archivos externos.
