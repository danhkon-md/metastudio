# Meta Studio

Herramienta local minimalista (en Light Mode) para generar copias limpias de archivos de audio con metadata estructurada, portadas estandarizadas y verificación automática.

![Meta Studio](docs/screenshot.png)

## Qué hace Meta Studio

- **Copy-First inviolable:** tus archivos de audio originales jamás se modifican, renombran ni borran.
- **Escritura atómica + Read-Back Verification:** cada pista se etiqueta en un archivo temporal, se reabre desde cero con `mutagen` para verificar duración, higiene binaria (ID3v2.4 sin ID3v1 en MP3/WAV; sin ID3 inicial en FLAC), portada incrustada y coincidencia exacta de todos los tags — y solo se publica al pasar la verificación.
- **Conversión opcional integrada (`WAV → FLAC` o `MP3 320k`):** incluye `ffmpeg` embebido vía `imageio-ffmpeg` (sin instalaciones previas) y elimina basura previa del DAW (`-map_metadata -1 -vn`).
- **Organización limpia multiplataforma:** normalización Unicode NFC para macOS/Linux/Windows, carpetas `{Artista}/[{Año}] {Álbum}/` (con `[Demo]` o `[WIP]` solo cuando aplica), portada `cover.jpg` (≤ 1400×1400 JPEG) y hojas `release-info.md` + `release-info.json` listas para copiar/pegar en Bandcamp o DistroKid y reimportables sin pérdida.
- **Géneros multi-valor reales:** múltiples etiquetas `GENRE` en FLAC (Vorbis Comments) y multi-valor real en MP3/WAV (ID3v2.4), nunca concatenados con comas.
- **Limpieza inteligente sin alterar tus mayúsculas:** elimina prefijos numéricos y sufijos técnicos (`_24bit`, `_48kHz`, `_master`, `_final`, `_v2`, etc.) sin aplicar Title Case automático.

---

## Descarga directa (GitHub Releases)

Descarga el paquete listo para usar desde la sección **[Releases](../../releases)** del repositorio (no requiere Python ni `ffmpeg` instalados):

- **macOS (Apple Silicon):** `Meta-Studio-macOS-arm64.zip`
- **Windows (x64):** `Meta-Studio-Windows-x64.zip`

### Instalación y aviso de seguridad

#### macOS (Gatekeeper)
La app cuenta con firma local ad-hoc (no notarizada por Apple). Tras descomprimir y mover `Meta Studio.app` a `/Applications`, si macOS muestra un aviso de desarrollador no verificado, ejecuta una sola vez en Terminal:

```bash
xattr -cr "/Applications/Meta Studio.app"
```

Luego ábrela normalmente desde `/Applications` o Spotlight.

#### Windows (SmartScreen y WebView2)
Descomprime `Meta-Studio-Windows-x64.zip` y ejecuta `Meta Studio\Meta Studio.exe`. Si Windows SmartScreen muestra un aviso de editor desconocido, haz clic en **Más información** → **Ejecutar de todas formas**.

> **Requisito en Windows 10:** Meta Studio usa **Microsoft Edge WebView2 Runtime** para la ventana nativa. Viene preinstalado en Windows 11 y en la mayoría de equipos con Windows 10 actualizado; si en Windows 10 no estuviera presente, puedes instalarlo gratis desde [Microsoft Edge WebView2](https://developer.microsoft.com/microsoft-edge/webview2/).

---

## Ejecutar desde código fuente (`uv`)

Requiere Python 3.12+ y [`uv`](https://docs.astral.sh/uv/).

```bash
git clone <URL-DEL-REPO> meta-studio
cd meta-studio
uv sync

# Modo ventana nativa de escritorio:
uv run meta-studio-desktop

# Modo web local (abre tu navegador automáticamente en http://127.0.0.1:8765):
uv run python scripts/launch.py
```

### Tests y compilación local

```bash
# Ejecutar toda la suite de pruebas:
uv run pytest -v

# Compilar ejecutable + .zip para tu sistema operativo actual (macOS o Windows):
uv run python scripts/build_app.py
```

---

## Licencia

Distribuido bajo licencia [MIT](LICENSE).
