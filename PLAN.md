# Plan de Implementación: Meta Studio

Plan por fases alineado estrictamente con `GEMINI.md`. No se avanza a la siguiente fase hasta que todos los tests de la fase actual pasen con `uv run pytest`.

---

## Estructura de Carpetas del Proyecto

```text
meta-studio/
├── GEMINI.md
├── PLAN.md
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .gitignore
├── presets/
│   ├── profiles.json                # Perfiles reutilizables (artista, sello, copyright, release type)
│   └── genres.json                  # Catálogo editable de géneros musicales
├── assets/
│   └── MetaStudio.icns              # Ícono macOS generado automáticamente (Pillow + iconutil)
├── scripts/
│   ├── launch.py                    # Launcher web local: inicia FastAPI/Uvicorn y abre el navegador
│   ├── desktop_entry.py             # Entry point dedicado para PyInstaller
│   └── build_app.py                 # Compilador unificado macOS (.app + .zip) y Windows (.exe + .zip) con ffmpeg embebido
├── Abrir Meta Studio.command        # Acceso directo doble clic en Finder (modo web)
├── src/
│   └── meta_studio/
│       ├── __init__.py              # Entry point CLI (`meta-studio`)
│       ├── runtime.py               # Resolución de rutas dev vs PyInstaller (`sys._MEIPASS` y Application Support)
│       ├── launcher.py              # Lógica del launcher web local y verificación (`--check`)
│       ├── desktop.py               # Ventana nativa macOS (`pywebview`) + Uvicorn en puerto libre con cierre limpio
│       ├── core/
│       │   ├── __init__.py
│       │   ├── models.py            # Modelos Pydantic v2 (álbum, tracks, opciones, verificación)
│       │   ├── sanitizer.py         # Normalización NFC macOS+Linux y cálculo de rutas destino
│       │   ├── cover.py             # Procesado Pillow (JPEG máx. 1400x1400, RGB)
│       │   ├── converter.py         # Conversión WAV -> FLAC / MP3 320k vía subprocess + ffmpeg
│       │   ├── tagger.py            # Copy-First + escritura atómica (FLAC Vorbis, MP3/WAV ID3v2.4)
│       │   ├── verifier.py          # Read-Back Verification desde cero con mutagen
│       │   └── pipeline.py          # Orquestador copy/convert -> tag -> atomic move -> verify
│       ├── services/
│       │   ├── __init__.py
│       │   ├── presets.py           # Lectura/escritura validada de profiles.json y genres.json
│       │   ├── cleaner.py           # Limpieza de nombres importados preservando mayúsculas/minúsculas
│       │   └── release_info.py      # Generador y lector bidireccional de release-info.md y .json
│       └── web/
│           ├── __init__.py
│           ├── app.py               # API FastAPI + servicio de archivos estáticos
│           └── static/
│               ├── index.html       # UI de una sola pantalla en Light Mode minimalista
│               ├── styles.css       # CSS moderno Light Mode (sin frameworks ni bundlers)
│               └── app.js           # Lógica Vanilla JS (presets, tracks, preview, export, reporte)
└── tests/
    ├── conftest.py                  # Fixtures: generación al vuelo de WAV/FLAC/MP3 silencio y portada en tmp_path
    ├── test_models.py
    ├── test_sanitizer.py
    ├── test_cover.py
    ├── test_converter.py
    ├── test_tagger_verifier.py
    ├── test_presets.py
    ├── test_cleaner.py
    ├── test_release_info.py
    ├── test_api.py
    └── test_runtime_and_desktop.py
```

---

## Fase 1 — Motor Core + Suite de Tests (`pytest`)

### Objetivo
Construir y validar el núcleo de validación, sanitización, conversión opcional, escritura segura de metadata y auto-verificación, sin dependencias de UI ni archivos externos.

### Dependencias (`uv`)
- Runtime: `mutagen`, `pydantic>=2`, `pillow`
- Desarrollo: `pytest`

### Entregables
1. **`src/meta_studio/core/models.py`**
   - Modelos Pydantic v2:
     - Obligatorios: `TITLE`, `ARTIST`, `ALBUM`, `ALBUMARTIST`, `DATE` (`YYYY-MM-DD`), `TRACKNUMBER`, `TOTALTRACKS`, `DISCNUMBER`, `GENRE` (`list[str]` no vacía), `RELEASETYPE`.
     - Derivados / con default automático: `YEAR` (`YYYY`) derivado automáticamente desde `DATE`; `ORIGINALDATE` (`YYYY-MM-DD`) usa `DATE` por defecto si no se especifica otra fecha.
     - Opcionales (rellenables vía presets): `LABEL`, `CATALOGNUMBER`, `COPYRIGHT` (si están presentes y no vacíos, se escriben y verifican exactamente en Read-Back Verification).
   - Tipos para `Status` (`Final | Demo | WIP`), modo de conversión de WAV (`keep | flac | mp3_320`), resultados de procesamiento por pista y reporte de `Read-Back Verification`.
2. **`src/meta_studio/core/sanitizer.py`**
   - Normalización Unicode NFC (`unicodedata.normalize("NFC", s)`).
   - Reemplazo de `:` por ` -`, eliminación de `/ \ * ? " < > |`, colapso de espacios múltiples y eliminación de puntos/espacios al final.
   - Construcción de carpeta destino: `{output_dir}/{AlbumArtist}/[{Year}] {Album}/` añadiendo ` [{Status}]` únicamente si `Status` es `Demo` o `WIP`.
   - Construcción de nombre de archivo: `{track:02d} - {Title}.{ext}` o `{disc}-{track:02d} - {Title}.{ext}` cuando haya más de 1 disco.
3. **`src/meta_studio/core/cover.py`**
   - Conversión a RGB y redimensionamiento proporcional con límite máximo de `1400x1400` en formato JPEG (`cover.jpg`), devolviendo bytes listos tanto para archivo en disco como para incrustación en tags.
4. **`src/meta_studio/core/converter.py`**
   - Conversión vía `ffmpeg` usando `subprocess.run(..., check=True)` exclusivamente cuando el origen sea `WAV` y se solicite salida `FLAC` o `MP3 320k` (`-b:a 320k`).
   - Pasa siempre `-map_metadata -1 -vn` para eliminar cualquier metadata o stream de video/portada basura previa del DAW.
   - Escribe siempre en archivo temporal, nunca modifica el archivo origen.
5. **`src/meta_studio/core/tagger.py`**
   - **Copy-First:** los archivos de origen jamás se modifican, renombran ni borran.
   - **Escritura atómica:** copia/convierte y aplica tags sobre un archivo temporal en el destino y ejecuta `os.replace` al nombre final solo al concluir sin errores.
   - **Limpieza previa obligatoria:** antes de escribir los nuevos tags en la copia temporal, elimina por completo cualquier tag y portada previa (Vorbis comments + `clear_pictures()` en FLAC; `delete()` / reinicio de `ID3` en MP3/WAV).
   - **Mapeo exacto de tags:**
     - **FLAC (Vorbis Comments):** campos en mayúsculas exactas, múltiples etiquetas `GENRE` reales (lista), `RELEASETYPE`, portada `Picture` (tipo 3, Front Cover, `image/jpeg`).
     - **MP3 / WAV (ID3v2.4):** guarda forzando explícitamente `v2_version=4` para blindar soporte multi-valor de `TCON`. Frames: `TIT2`, `TPE1`, `TALB`, `TPE2`, `TDRC`, `TDOR`, `TXXX:YEAR`, `TRCK`, `TPOS`, `TCON` multi-valor real (lista de géneros sin concatenar con comas), `TXXX:MusicBrainz Album Type` (`RELEASETYPE`), `TPUB` (`LABEL`, si existe), `TXXX:CATALOGNUMBER` (`CATALOGNUMBER`, si existe), `TCOP` (`COPYRIGHT`, si existe), portada `APIC` (tipo 3, `image/jpeg`).
6. **`src/meta_studio/core/verifier.py`**
   - **Read-Back Verification:** reabre desde cero cada archivo final con `mutagen` y verifica:
     - Duración válida (> 0 y consistente con la fuente).
     - Portada incrustada presente y coincidente en formato/contenido (o ausente si no se proveyó portada).
     - Coincidencia exacta de los campos obligatorios + derivados (`YEAR`, `ORIGINALDATE`), la lista exacta de géneros en orden, y los opcionales (`LABEL`, `CATALOGNUMBER`, `COPYRIGHT`) cuando estén presentes (y ausencia cuando no se hayan proporcionado).
     - En MP3/WAV, confirmación de cabecera ID3v2.4.
7. **`src/meta_studio/core/pipeline.py`**
   - Orquesta procesamiento del álbum completo (carpeta sanitizada, `cover.jpg`, copia/conversión, taggeo atómico y read-back verification por pista) con limpieza automática de archivos temporales ante fallos.
8. **Suite de tests (`tests/`)**
   - `tests/conftest.py`: genera audios cortos de silencio (WAV, FLAC y MP3) e imágenes de portada sintéticas al vuelo en `tmp_path`.
   - `test_models.py`, `test_sanitizer.py`, `test_cover.py`, `test_converter.py`, `test_tagger_verifier.py` (incluyendo pruebas de Copy-First mediante hash SHA-256 de archivos origen, escritura atómica y fallo deliberado de Read-Back Verification).

### Comandos de Verificación (Fase 1)
```bash
cd meta-studio
uv sync
uv run pytest -v tests/test_models.py tests/test_sanitizer.py tests/test_cover.py tests/test_converter.py tests/test_tagger_verifier.py
uv run pytest
```

---

## Fase 2 — Presets, Limpiador Inteligente y `release-info`

### Objetivo
Incorporar gestión persistente de perfiles y géneros, limpieza inteligente de nombres sin alterar capitalización del usuario, y generación/lectura bidireccional de `release-info.md` y `release-info.json`.

### Entregables
1. **`presets/profiles.json` y `presets/genres.json`**
   - Archivos JSON iniciales con perfiles base y catálogo de géneros editable.
2. **`src/meta_studio/services/presets.py`**
   - Carga, validación con Pydantic v2 y persistencia atómica de `profiles.json` y `genres.json`.
   - Operaciones CRUD para perfiles y gestión de lista de géneros sin duplicados.
3. **`src/meta_studio/services/cleaner.py`**
   - Limpia nombres de archivos importados eliminando extensión, prefijos numéricos (`01 - `, `01_`, `1.`, `1-02 `), guiones bajos (`_` → espacio) y sufijos técnicos al final (`_master`, `_final`, `_mix`, `_v2`, `final_v3`, etc., insensible a mayúsculas en el sufijo).
   - Colapsa espacios múltiples y recorta extremos **sin aplicar Title Case automático** (preserva exactamente mayúsculas/minúsculas originales como `DJ`, `Lo-Fi`, `iPhone`, `NASA`, `all lowercase`).
4. **`src/meta_studio/services/release_info.py`**
   - Generación atómica de `release-info.json` (fuente estructurada) y `release-info.md` (resumen legible en Markdown con metadata del lanzamiento, tracklist con duraciones y estado de verificación).
   - Soporte de campos opcionales de distribución y créditos tanto en `release-info.json` como en `release-info.md`: género primario y secundario para distribuidora, idioma, instrumental (`bool`), explícito (`bool`), créditos, `UPC` del lanzamiento e `ISRC` por track.
   - Lectura bidireccional desde `release-info.json` y desde `release-info.md` para reimportar/reprocesar lanzamientos previos (incluyendo los campos opcionales de distribución y créditos).
   - Integración en `core/pipeline.py` para emitir siempre `cover.jpg`, `release-info.md` y `release-info.json` dentro de la carpeta del álbum.
5. **Suite de tests**
   - `tests/test_presets.py`, `tests/test_cleaner.py`, `tests/test_release_info.py` + verificación end-to-end del pipeline generando `cover.jpg`, `release-info.md` y `release-info.json`.

### Comandos de Verificación (Fase 2)
```bash
cd meta-studio
uv run pytest -v tests/test_presets.py tests/test_cleaner.py tests/test_release_info.py
uv run pytest
```

---

## Fase 3 — API FastAPI + Interfaz Web Light Mode + Launcher

### Objetivo
Exponer toda la funcionalidad mediante una API FastAPI local, una interfaz web minimalista de una sola pantalla en Light Mode (HTML5 + CSS + Vanilla JS sin bundlers) y un script launcher que levante el servidor y abra el navegador automáticamente.

### Dependencias (`uv`)
- Runtime: `fastapi`, `uvicorn`, `python-multipart`
- Desarrollo: `httpx`

### Entregables
1. **`src/meta_studio/web/app.py`**
   - Endpoints REST:
     - `GET /api/health`
     - `GET/POST/PUT/DELETE /api/presets/profiles` y `GET/PUT /api/presets/genres`
     - `GET/PUT /api/settings/output-dir` (carpeta de salida por defecto persistente)
     - `POST /api/pick-folder` (selector nativo de carpeta en macOS vía `osascript`, con fallback manual si no está disponible o se cancela)
     - `POST /api/open-folder` (abre la carpeta generada en Finder)
     - `POST /api/clean-names` (limpieza de nombres respetando mayúsculas/minúsculas)
     - `POST /api/preview-paths` (previsualización de carpeta destino y nombres de archivo sanitizados)
     - `POST /api/import-release-info` (carga de `release-info.json` o `release-info.md`)
     - `POST /api/process` (recepción de pistas + portada + metadata, ejecución Copy-First/atómica y retorno del reporte completo de Read-Back Verification)
   - Servicio de archivos estáticos desde `src/meta_studio/web/static/` y `/` devolviendo `index.html`.
2. **`src/meta_studio/web/static/{index.html,styles.css,app.js}`**
   - Interfaz de **una sola pantalla** en **Light Mode minimalista** (sin Node, sin frameworks externos, sin bundlers).
   - Funcionalidades:
     - Carga de perfil desde `profiles.json` y guardado rápido de perfil.
     - Formulario de metadata del lanzamiento (campos obligatorios, derivados automáticos `YEAR`/`ORIGINALDATE`, opcionales rellenables por preset, selector multi-valor de géneros como chips reales, `Status`, carpeta de salida por defecto persistente con selector nativo y conversión WAV).
     - Bloque colapsable opcional para campos extra de distribución y créditos (género primario/secundario de distribuidora, idioma, instrumental, explícito, créditos, UPC e ISRC por track) para mantener la pantalla principal limpia y ultrarrápida.
     - Selección/arrastre de archivos de audio y portada con vista previa.
     - Tabla editable de pistas con reordenamiento **drag & drop** de filas que recalcula automáticamente `TRACKNUMBER`, botón de limpieza inteligente en un clic y vista previa en vivo de la ruta final sanitizada.
     - Importación de `release-info.json`/`release-info.md` previo.
     - Panel de progreso, botón para abrir carpeta generada en Finder (`POST /api/open-folder`) y reporte detallado de `Read-Back Verification` por pista.
3. **`scripts/launch.py` y CLI (`src/meta_studio/__init__.py`)**
   - Inicia `uvicorn` en `127.0.0.1` (puerto por defecto `8765`, configurable por `--host`/`--port`).
   - Espera a que `/api/health` responda y abre la URL automáticamente con `webbrowser.open`.
   - Flags útiles: `--no-browser` y `--check` (verifica importación de la app y disponibilidad de `ffmpeg`).
4. **Suite de tests (`tests/test_api.py`)**
   - Pruebas con `fastapi.testclient.TestClient` cubriendo todos los endpoints y un flujo completo de subida, conversión/taggeo, generación de `cover.jpg` + `release-info.{md,json}` y verificación `Read-Back`.

### Comandos de Verificación (Fase 3)
```bash
cd meta-studio
uv sync
uv run pytest -v
uv run python scripts/launch.py --check
# Arranque interactivo local:
uv run python scripts/launch.py
```

---

## Fase 4 — App Nativa macOS (`Meta Studio.app`) con `pywebview` + `PyInstaller`

### Objetivo
Empaquetar Meta Studio como aplicación nativa compilada para macOS (`dist/Meta Studio.app`) manteniendo intactos el modo web/dev y toda la suite de pruebas en verde.

### Dependencias (`uv`)
- Runtime: `pywebview`
- Desarrollo: `pyinstaller`

### Entregables
1. **`src/meta_studio/runtime.py`**
   - Resuelve rutas de solo lectura (`static/` y plantillas `presets/`) en desarrollo y en binario PyInstaller (`sys._MEIPASS`).
   - Resuelve la carpeta escribible de datos del usuario: `~/Library/Application Support/Meta Studio/` en modo compilado (copiando atómicamente `profiles.json` y `genres.json` iniciales solo si no existen), manteniendo `presets/` del repo en desarrollo.
2. **Resolver de `ffmpeg` en `core/converter.py` y `launcher.py`**
   - Busca primero con `shutil.which("ffmpeg")` y, si no está en `PATH` (típico en apps GUI de macOS), revisa explícitamente `/opt/homebrew/bin/ffmpeg` y `/usr/local/bin/ffmpeg`.
3. **`src/meta_studio/desktop.py` y `scripts/desktop_entry.py`**
   - Levanta FastAPI/Uvicorn en `127.0.0.1` en un puerto libre automático (socket puerto `0`) en un hilo daemon, espera `/api/health` y abre una ventana nativa con `webview.create_window("Meta Studio", url, width=1280, height=840, min_size=(960, 680))`.
   - Integra el diálogo nativo de selección de carpeta (`webview.FileDialog.FOLDER`) con fallback a `osascript` y garantiza apagado limpio del servidor al cerrar la ventana.
4. **Ícono y script de compilación (`scripts/build_app.py`)**
   - Genera `assets/MetaStudio.icns` en Light Mode minimalista usando `Pillow` + `iconutil`.
   - Compila `dist/Meta Studio.app` con PyInstaller `--windowed --onedir`, incrusta `static/` y plantillas `presets/`, actualiza `Info.plist` (`CFBundleIdentifier`, versión `1.0.0`) y aplica firma ad-hoc (`codesign --force --deep --sign -`).
5. **Tests (`tests/test_runtime_and_desktop.py`)**
   - Pruebas unitarias de `runtime.py` (dev vs frozen + seeding sin sobrescribir), resolución de `ffmpeg` sin `PATH`, puerto libre y ciclo de vida limpio de `LocalUvicornServer`.

### Comandos de Verificación (Fase 4)
```bash
cd meta-studio
uv run pytest -v
uv run python scripts/launch.py --check
uv run meta-studio-desktop --check
uv run python scripts/build_app.py
```

---

## Fase 5 — Publicación Pública Multiplataforma y GitHub Releases

### Objetivo
Dejar Meta Studio listo para publicación pública en GitHub con cero dependencias externas (`imageio-ffmpeg`), soporte real macOS + Windows + Linux, build unificado y GitHub Actions para CI y Releases automáticos.

### Entregables
1. **Limpieza pública:** `presets/profiles.json` genérico (`Independent Artist` y `Demo / WIP`), `LICENSE` (MIT), `.gitignore` completo y eliminación de rutas absolutas locales.
2. **`ffmpeg` embebido (`imageio-ffmpeg`):** orden de resolución `shutil.which("ffmpeg")` → `/opt/homebrew/bin/ffmpeg` / `/usr/local/bin/ffmpeg` → `imageio_ffmpeg.get_ffmpeg_exe()`, empaquetado con `--collect-binaries imageio_ffmpeg`.
3. **Soporte multiplataforma (`runtime.py`, `web/app.py`, `scripts/build_app.py`):**
   - Rutas de datos en modo `frozen`: `~/Library/Application Support/Meta Studio` (macOS), `%APPDATA%\Meta Studio` (Windows) y `~/.local/share/meta-studio` (Linux).
   - Selector nativo `/api/pick-folder` (`osascript`, PowerShell `FolderBrowserDialog`, `zenity`/`kdialog`) y `/api/open-folder` (`open --`, `os.startfile`, `xdg-open`).
   - Script unificado `scripts/build_app.py` generando `Meta-Studio-macOS-arm64.zip` (con `ditto`) y `Meta-Studio-Windows-x64.zip` (con `.ico`).
4. **CI/CD (`.github/workflows/release.yml`) y documentación (`README.md`):** tests en macOS/Windows y publicación automática de binarios al subir tags `v*.*.*`.

### Comandos de Verificación (Fase 5)
```bash
cd meta-studio
uv run pytest -v
uv run python scripts/build_app.py
```
