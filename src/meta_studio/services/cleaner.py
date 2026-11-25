from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_AUDIO_EXTENSIONS = {
    ".wav",
    ".flac",
    ".mp3",
    ".aif",
    ".aiff",
    ".m4a",
    ".ogg",
    ".opus",
}

# Prefijos numéricos:
# - Disco-pista: `1-02 - `, `1-02_`, `2.03 `
# - Pista con separador explícito: `01 - `, `01_`, `1. `, `03) `
# - Pista con cero inicial + espacio: `01 Intro`
# Protege títulos como "99 Luftballons", "7 Rings" y años "1984".
_LEADING_TRACK_PREFIX_RE = re.compile(
    r"^(?:\d{1,2}[-.]\d{1,3}(?:\s*[-._)]\s*|\s+)|\d{1,3}\s*[-._)]\s*|0\d{1,2}\s+)"
)

# Tokens técnicos inequívocos (no son palabras comunes de títulos)
_UNAMBIGUOUS_TECH_TOKEN = (
    r"(?:"
    r"v\d+(?:\.\d+)*"
    r"|rev\d+"
    r"|take[\s_]*\d+"
    r"|16[- _]?bit|24[- _]?bit|32[- _]?bit"
    r"|44(?:\.1)?[\s_]*khz|44(?:\.1)?k|48[\s_]*khz|48k|88\.2[\s_]*khz|88\.2k|96[\s_]*khz|96k|192[\s_]*khz|192k|320[\s_]*kbps|320k"
    r"|unmastered|pre[- _]?master|mastered"
    r"|rough[\s_]+mix|rough|mixdown|wip|bounce"
    r")"
)

# Palabras técnicas ambiguas que solo se quitan si llevan separador de puntuación (`_`, `-`, `.`) o paréntesis/corchetes
_AMBIGUOUS_TECH_TOKEN = r"(?:master|final|mix|demo|render|export)"

_ANY_TECH_TOKEN = rf"(?:{_UNAMBIGUOUS_TECH_TOKEN}|{_AMBIGUOUS_TECH_TOKEN})"

# Sufijo técnico entre paréntesis o corchetes al final, ej: "(final master)", "[24bit 48kHz]"
_TRAILING_BRACKETED_TECH_RE = re.compile(
    rf"[\s_\-–—]*[\(\[]\s*{_ANY_TECH_TOKEN}(?:[\s_\-–—/]+{_ANY_TECH_TOKEN})*\s*[\)\]]\s*$",
    re.IGNORECASE,
)

# Sufijo inequívoco separado por puntuación o espacio
_TRAILING_UNAMBIGUOUS_RE = re.compile(
    rf"(?:[\s_\-–—.]+){_UNAMBIGUOUS_TECH_TOKEN}\s*$",
    re.IGNORECASE,
)

# Sufijo ambiguo solo cuando está separado por puntuación explícita (`_`, `-`, `–`, `—`, `.`)
_TRAILING_AMBIGUOUS_RE = re.compile(
    rf"(?:\s*[_\-–—.]\s*){_AMBIGUOUS_TECH_TOKEN}\s*$",
    re.IGNORECASE,
)

_TRAILING_SEPARATORS_RE = re.compile(r"[\s_\-–—]+$")
_MULTI_SPACE_RE = re.compile(r"\s+")


def clean_track_title(raw_filename: str) -> str:
    """Limpia nombres de archivo importados preservando EXACTAMENTE mayúsculas/minúsculas.

    - Elimina extensión de audio conocida.
    - Quita prefijos numéricos solo con separador `[-_.)]` o cero inicial (`01 Intro`),
      sin romper títulos como `"99 Luftballons"` o `"7 Rings"`.
    - Elimina sufijos técnicos ANTES de convertir `_` en espacio, exigiendo separador
      de puntuación para palabras ambiguas (`master`, `final`, `mix`, `demo`, `render`, `export`)
      para no romper títulos como `"The Master"`.
    - NUNCA aplica Title Case automático.
    """
    text = unicodedata.normalize("NFC", Path(raw_filename).name).strip()
    if not text:
        return ""

    # 1. Eliminar extensión de audio conocida (respetando títulos con puntos como "Track 2.5")
    dot_idx = text.rfind(".")
    if dot_idx > 0 and text[dot_idx:].lower() in _AUDIO_EXTENSIONS:
        text = text[:dot_idx]

    original_stem = text.strip()

    # 2. Quitar prefijo numérico inicial (protegiendo "99 Luftballons", "7 Rings", "1984")
    without_prefix = _LEADING_TRACK_PREFIX_RE.sub("", text, count=1).strip()
    if without_prefix:
        text = without_prefix

    # 3. Eliminar iterativamente sufijos técnicos ANTES de convertir `_` en espacio
    while True:
        candidate = _TRAILING_BRACKETED_TECH_RE.sub("", text)
        if candidate == text:
            candidate = _TRAILING_UNAMBIGUOUS_RE.sub("", text)
        if candidate == text:
            candidate = _TRAILING_AMBIGUOUS_RE.sub("", text)
        candidate = _TRAILING_SEPARATORS_RE.sub("", candidate).strip()
        if not candidate or candidate == text:
            break
        text = candidate

    # 4. Convertir guiones bajos restantes en espacios y colapsar espacios
    text = text.replace("_", " ")
    text = _MULTI_SPACE_RE.sub(" ", text).strip()
    return text if text else original_stem


def clean_track_titles(filenames: list[str]) -> list[str]:
    """Aplica `clean_track_title` a una lista de nombres preservando el orden."""
    return [clean_track_title(name) for name in filenames]
