#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PROJECT_ROOT / "assets"
ICNS_PATH = ASSETS_DIR / "MetaStudio.icns"
ICO_PATH = ASSETS_DIR / "MetaStudio.ico"
APP_NAME = "Meta Studio"
BUNDLE_IDENTIFIER = "app.metastudio.desktop"
APP_VERSION = "1.0.0"

ICONSET_SIZES: tuple[tuple[str, int], ...] = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)

WINDOWS_ICO_SIZES: list[tuple[int, int]] = [
    (16, 16),
    (24, 24),
    (32, 32),
    (48, 48),
    (64, 64),
    (128, 128),
    (256, 256),
]


def render_icon_master(size: int = 2048) -> Image.Image:
    """Dibuja un ícono minimalista en Light Mode con ondas de audio y sello verde verificado."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = int(size * 0.095)
    radius = int(size * 0.205)

    shadow_offset = int(size * 0.014)
    draw.rounded_rectangle(
        [pad, pad + shadow_offset, size - pad, size - pad + shadow_offset],
        radius=radius,
        fill=(24, 24, 22, 36),
    )

    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=radius,
        fill=(250, 249, 246, 255),
        outline=(214, 214, 207, 255),
        width=max(2, int(size * 0.012)),
    )

    bar_heights = (0.18, 0.34, 0.46, 0.30, 0.20)
    bar_width = int(size * 0.068)
    bar_gap = int(size * 0.044)
    total_w = len(bar_heights) * bar_width + (len(bar_heights) - 1) * bar_gap
    start_x = (size - total_w) // 2
    center_y = int(size * 0.485)
    bar_radius = bar_width // 2

    for idx, frac in enumerate(bar_heights):
        h = int(size * frac)
        x0 = start_x + idx * (bar_width + bar_gap)
        y0 = center_y - h // 2
        x1 = x0 + bar_width
        y1 = y0 + h
        draw.rounded_rectangle(
            [x0, y0, x1, y1],
            radius=bar_radius,
            fill=(24, 24, 27, 255),
        )

    badge_r = int(size * 0.125)
    badge_cx = int(size * 0.72)
    badge_cy = int(size * 0.71)
    ring_w = int(size * 0.022)

    draw.ellipse(
        [
            badge_cx - badge_r - ring_w,
            badge_cy - badge_r - ring_w,
            badge_cx + badge_r + ring_w,
            badge_cy + badge_r + ring_w,
        ],
        fill=(250, 249, 246, 255),
    )
    draw.ellipse(
        [
            badge_cx - badge_r,
            badge_cy - badge_r,
            badge_cx + badge_r,
            badge_cy + badge_r,
        ],
        fill=(21, 128, 61, 255),
    )
    check_pts = [
        (badge_cx - int(badge_r * 0.44), badge_cy + int(badge_r * 0.02)),
        (badge_cx - int(badge_r * 0.12), badge_cy + int(badge_r * 0.34)),
        (badge_cx + int(badge_r * 0.48), badge_cy - int(badge_r * 0.30)),
    ]
    draw.line(
        check_pts,
        fill=(255, 255, 255, 255),
        width=max(4, int(size * 0.032)),
        joint="curve",
    )

    return img


def generate_icns(icns_path: Path = ICNS_PATH, *, force: bool = False) -> Path:
    """Genera `assets/MetaStudio.icns` usando Pillow + `iconutil` de macOS."""
    if icns_path.is_file() and not force:
        print(f"Ícono macOS reutilizado: {icns_path}")
        return icns_path

    if shutil.which("iconutil") is None:
        raise RuntimeError("No se encontró `iconutil` (herramienta estándar de macOS).")

    icns_path.parent.mkdir(parents=True, exist_ok=True)
    master = render_icon_master(2048)

    with tempfile.TemporaryDirectory(prefix="metastudio_icon_") as tmp_dir_str:
        iconset_dir = Path(tmp_dir_str) / "MetaStudio.iconset"
        iconset_dir.mkdir(parents=True, exist_ok=True)

        for filename, dim in ICONSET_SIZES:
            resized = master.resize((dim, dim), Image.Resampling.LANCZOS)
            resized.save(iconset_dir / filename, format="PNG")

        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
            check=True,
            capture_output=True,
            text=True,
        )

    print(f"Ícono macOS generado: {icns_path}")
    return icns_path


def generate_ico(ico_path: Path = ICO_PATH, *, force: bool = False) -> Path:
    """Genera `assets/MetaStudio.ico` multi-resolución para Windows con Pillow."""
    if ico_path.is_file() and not force:
        print(f"Ícono Windows reutilizado: {ico_path}")
        return ico_path

    ico_path.parent.mkdir(parents=True, exist_ok=True)
    master = render_icon_master(1024)
    master.save(ico_path, format="ICO", sizes=WINDOWS_ICO_SIZES)
    print(f"Ícono Windows generado: {ico_path}")
    return ico_path


def patch_info_plist(app_bundle: Path, *, version: str = APP_VERSION) -> None:
    """Asegura metadatos limpios del bundle macOS en Info.plist."""
    plist_path = app_bundle / "Contents" / "Info.plist"
    with plist_path.open("rb") as fh:
        plist = plistlib.load(fh)

    plist["CFBundleName"] = APP_NAME
    plist["CFBundleDisplayName"] = APP_NAME
    plist["CFBundleIdentifier"] = BUNDLE_IDENTIFIER
    plist["CFBundleShortVersionString"] = version
    plist["CFBundleVersion"] = version
    plist["NSHighResolutionCapable"] = True
    plist["LSMinimumSystemVersion"] = "12.0"

    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)


def _zip_directory(source_dir: Path, zip_path: Path) -> Path:
    """Empaqueta una carpeta preservando el directorio raíz en el archivo `.zip`."""
    if zip_path.exists():
        zip_path.unlink()
    base_parent = source_dir.parent
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, arcname=file_path.relative_to(base_parent))
    return zip_path


def build_app(
    *,
    rebuild_icon: bool = False,
    create_zip: bool = True,
    version: str = APP_VERSION,
) -> tuple[Path, Path | None]:
    """Compila Meta Studio en macOS (`.app` + `.zip`) o Windows (`.exe` + `.zip`)."""
    is_mac = sys.platform == "darwin"
    is_win = sys.platform == "win32"
    if not (is_mac or is_win):
        raise RuntimeError(
            f"Plataforma no soportada para compilación empaquetada: {sys.platform}"
        )

    icon_path = (
        generate_icns(ICNS_PATH, force=rebuild_icon)
        if is_mac
        else generate_ico(ICO_PATH, force=rebuild_icon)
    )

    build_dir = PROJECT_ROOT / "build"
    dist_dir = PROJECT_ROOT / "dist"
    app_bundle = dist_dir / f"{APP_NAME}.app"
    onedir_folder = dist_dir / APP_NAME

    for path in (build_dir, app_bundle, onedir_folder):
        if path.exists():
            shutil.rmtree(path)

    static_src = PROJECT_ROOT / "src" / "meta_studio" / "web" / "static"
    presets_src = PROJECT_ROOT / "presets"
    entry_script = PROJECT_ROOT / "scripts" / "desktop_entry.py"
    data_sep = os.pathsep

    with tempfile.TemporaryDirectory(prefix="metastudio_presets_stage_") as presets_stage_str:
        staged_presets = Path(presets_stage_str) / "presets"
        staged_presets.mkdir(parents=True, exist_ok=True)
        for preset_file in ("profiles.json", "genres.json"):
            shutil.copy2(presets_src / preset_file, staged_presets / preset_file)

        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--windowed",
            "--onedir",
            "--noconfirm",
            "--clean",
            "--name",
            APP_NAME,
            "--icon",
            str(icon_path),
            "--specpath",
            str(build_dir),
            "--workpath",
            str(build_dir / "work"),
            "--distpath",
            str(dist_dir),
            "--paths",
            str(PROJECT_ROOT / "src"),
            "--add-data",
            f"{static_src}{data_sep}meta_studio/web/static",
            "--add-data",
            f"{staged_presets}{data_sep}presets",
            # Uvicorn + FastAPI + Mutagen + FFmpeg embebido (cero dependencias externas)
            "--hidden-import",
            "uvicorn.logging",
            "--hidden-import",
            "uvicorn.loops.auto",
            "--hidden-import",
            "uvicorn.loops.asyncio",
            "--hidden-import",
            "uvicorn.protocols.http.auto",
            "--hidden-import",
            "uvicorn.protocols.http.h11_impl",
            "--hidden-import",
            "uvicorn.protocols.websockets.auto",
            "--hidden-import",
            "uvicorn.lifespan.on",
            "--collect-submodules",
            "mutagen",
            "--collect-all",
            "imageio_ffmpeg",
        ]

        if is_mac:
            cmd.extend(
                [
                    "--osx-bundle-identifier",
                    BUNDLE_IDENTIFIER,
                    "--hidden-import",
                    "webview.platforms.cocoa",
                    "--hidden-import",
                    "objc",
                    "--hidden-import",
                    "AppKit",
                    "--hidden-import",
                    "Foundation",
                    "--hidden-import",
                    "WebKit",
                    "--hidden-import",
                    "Quartz",
                    "--hidden-import",
                    "UniformTypeIdentifiers",
                    "--hidden-import",
                    "PyObjCTools",
                    "--hidden-import",
                    "PyObjCTools.AppHelper",
                ]
            )
        else:
            cmd.extend(
                [
                    "--hidden-import",
                    "webview.platforms.edgechromium",
                    "--collect-data",
                    "webview",
                    "--collect-binaries",
                    "webview",
                    "--collect-submodules",
                    "webview.platforms",
                ]
            )

        cmd.append(str(entry_script))

        print(f"Compilando {APP_NAME} ({sys.platform}) con PyInstaller (--windowed --onedir)...")
        subprocess.run(cmd, check=True, cwd=str(PROJECT_ROOT))

    zip_artifact: Path | None = None
    if is_mac:
        if not app_bundle.is_dir():
            raise RuntimeError(f"PyInstaller no generó el bundle esperado en {app_bundle}")

        patch_info_plist(app_bundle, version=version)

        if onedir_folder.is_dir():
            shutil.rmtree(onedir_folder)

        # Aserción explícita: verificar que ffmpeg embebido esté presente dentro del bundle
        bundled_ffmpegs = [
            p
            for p in app_bundle.rglob("imageio_ffmpeg/binaries/ffmpeg-*")
            if p.is_file()
        ]
        if not bundled_ffmpegs:
            raise RuntimeError(
                f"Falta el binario embebido imageio_ffmpeg/binaries/ffmpeg-* dentro de {app_bundle}"
            )
        print(f"FFmpeg embebido verificado: {bundled_ffmpegs[0].relative_to(app_bundle)}")

        print("Aplicando firma ad-hoc con codesign...")
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)],
            check=True,
        )
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(app_bundle)],
            check=True,
        )

        if create_zip:
            arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
            zip_artifact = dist_dir / f"Meta-Studio-macOS-{arch}.zip"
            if zip_artifact.exists():
                zip_artifact.unlink()
            print(f"Empaquetando {zip_artifact.name} con ditto (preservando symlinks)...")
            subprocess.run(
                [
                    "ditto",
                    "-c",
                    "-k",
                    "--sequesterRsrc",
                    "--keepParent",
                    str(app_bundle),
                    str(zip_artifact),
                ],
                check=True,
            )

        print("")
        print("=" * 64)
        print(f"Compilación completada: {app_bundle}")
        if zip_artifact:
            print(f"Paquete listo para Release: {zip_artifact}")
        print(f"Bundle ID: {BUNDLE_IDENTIFIER} | Versión: {version}")
        print("=" * 64)
        print("Para probar la app ahora:")
        print(f'  open "{app_bundle}"')
        print("")
        print("Para instalarla en /Applications:")
        print(f'  rm -rf "/Applications/{APP_NAME}.app" && cp -R "{app_bundle}" "/Applications/{APP_NAME}.app"')
        print(f'  open "/Applications/{APP_NAME}.app"')
        print("=" * 64)
        return app_bundle, zip_artifact

    # Windows
    exe_path = onedir_folder / f"{APP_NAME}.exe"
    if not exe_path.is_file():
        raise RuntimeError(f"PyInstaller no generó el ejecutable esperado en {exe_path}")

    # Aserciones explícitas: ffmpeg embebido y WebView2Loader.dll dentro del paquete Windows
    bundled_ffmpegs = [
        p
        for p in onedir_folder.rglob("imageio_ffmpeg/binaries/ffmpeg-*.exe")
        if p.is_file()
    ]
    if not bundled_ffmpegs:
        raise RuntimeError(
            f"Falta el binario embebido imageio_ffmpeg/binaries/ffmpeg-*.exe dentro de {onedir_folder}"
        )
    webview_loaders = [
        p
        for p in onedir_folder.rglob("webview/lib/runtimes/win-x64/native/WebView2Loader.dll")
        if p.is_file()
    ]
    if not webview_loaders:
        raise RuntimeError(
            f"Falta webview/lib/runtimes/win-x64/native/WebView2Loader.dll dentro de {onedir_folder}"
        )
    print(f"FFmpeg embebido verificado: {bundled_ffmpegs[0].relative_to(onedir_folder)}")
    print(f"WebView2Loader verificado: {webview_loaders[0].relative_to(onedir_folder)}")

    if create_zip:
        zip_artifact = dist_dir / "Meta-Studio-Windows-x64.zip"
        print(f"Empaquetando {zip_artifact.name}...")
        _zip_directory(onedir_folder, zip_artifact)

    print("")
    print("=" * 64)
    print(f"Compilación completada: {exe_path}")
    if zip_artifact:
        print(f"Paquete listo para Release: {zip_artifact}")
    print(f"Versión: {version}")
    print("=" * 64)
    return exe_path, zip_artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compila Meta Studio para macOS (.app + .zip) o Windows (.exe + .zip)."
    )
    parser.add_argument(
        "--rebuild-icon",
        action="store_true",
        help="Regenera el ícono (.icns / .ico) aunque ya exista.",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Omite la generación del archivo .zip final.",
    )
    parser.add_argument(
        "--version",
        default=APP_VERSION,
        help=f"Versión del paquete (default: {APP_VERSION}).",
    )
    args = parser.parse_args(argv)
    build_app(
        rebuild_icon=args.rebuild_icon,
        create_zip=not args.no_zip,
        version=args.version.lstrip("v"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
