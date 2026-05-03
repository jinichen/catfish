#!/usr/bin/env python3
"""把 app-icon-master.svg 渲染成 Tauri 全套 icon.

用法:
    cd branding && python3 render_icons.py

会写到:
    edge/companion-app/src-tauri/icons/
        ├ icon.png  (1024)
        ├ icon.ico  (multi-size 16/32/48/64/128/256)
        ├ icon.icns (multi-size 16~1024)
        ├ 32x32.png / 64x64.png / 128x128.png / 128x128@2x.png
        ├ Square*Logo.png  (Windows tiles)
        ├ StoreLogo.png
        ├ ios/AppIcon-*.png
        └ android/mipmap-*/ic_launcher*.png
"""
from __future__ import annotations

import io
from pathlib import Path

import cairosvg  # type: ignore
import icnsutil  # type: ignore
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent  # catfish/
SVG = ROOT / "branding" / "app-icon-master.svg"
ICONS = ROOT / "edge" / "companion-app" / "src-tauri" / "icons"


def render_png(size: int) -> Image.Image:
    """SVG → PNG (Pillow Image), 指定边长."""
    png_bytes = cairosvg.svg2png(url=str(SVG), output_width=size, output_height=size)
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def write(size: int, path: Path) -> None:
    img = render_png(size)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    print(f"  wrote {path.relative_to(ROOT)} ({size}x{size})")


def main() -> None:
    print(f"Rendering from {SVG.relative_to(ROOT)} → {ICONS.relative_to(ROOT)}")

    # ── Tauri 顶层 ──
    write(1024, ICONS / "icon.png")
    write(32, ICONS / "32x32.png")
    write(64, ICONS / "64x64.png")
    write(128, ICONS / "128x128.png")
    write(256, ICONS / "128x128@2x.png")  # @2x = 128*2

    # ── Windows tiles ──
    win_tiles = {
        "Square30x30Logo.png": 30,
        "Square44x44Logo.png": 44,
        "Square71x71Logo.png": 71,
        "Square89x89Logo.png": 89,
        "Square107x107Logo.png": 107,
        "Square142x142Logo.png": 142,
        "Square150x150Logo.png": 150,
        "Square284x284Logo.png": 284,
        "Square310x310Logo.png": 310,
        "StoreLogo.png": 50,
    }
    for name, size in win_tiles.items():
        write(size, ICONS / name)

    # ── Windows .ico (multi-size embedded) ──
    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_imgs = [render_png(s) for s in ico_sizes]
    ico_path = ICONS / "icon.ico"
    ico_imgs[0].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in ico_sizes],
        append_images=ico_imgs[1:],
    )
    print(f"  wrote {ico_path.relative_to(ROOT)} (multi-size {ico_sizes})")

    # ── macOS .icns ──
    icns_sizes = [16, 32, 64, 128, 256, 512, 1024]
    icns = icnsutil.IcnsFile()
    for s in icns_sizes:
        buf = io.BytesIO()
        render_png(s).save(buf, "PNG")
        # icnsutil 用 type code 区分: ic07=128, ic08=256, ic09=512, ic10=1024 (@2x), etc.
        type_map = {16: "is32", 32: "il32", 64: "icp6", 128: "ic07",
                    256: "ic08", 512: "ic09", 1024: "ic10"}
        icns.add_media(type_map[s], data=buf.getvalue())
    icns_path = ICONS / "icon.icns"
    icns.write(str(icns_path))
    print(f"  wrote {icns_path.relative_to(ROOT)} (multi-size {icns_sizes})")

    # ── iOS ──
    ios_dir = ICONS / "ios"
    ios_specs = {
        "AppIcon-20x20@1x.png": 20,
        "AppIcon-20x20@2x.png": 40,
        "AppIcon-20x20@2x-1.png": 40,
        "AppIcon-20x20@3x.png": 60,
        "AppIcon-29x29@1x.png": 29,
        "AppIcon-29x29@2x.png": 58,
        "AppIcon-29x29@2x-1.png": 58,
        "AppIcon-29x29@3x.png": 87,
        "AppIcon-40x40@1x.png": 40,
        "AppIcon-40x40@2x.png": 80,
        "AppIcon-40x40@2x-1.png": 80,
        "AppIcon-40x40@3x.png": 120,
        "AppIcon-60x60@2x.png": 120,
        "AppIcon-60x60@3x.png": 180,
        "AppIcon-76x76@1x.png": 76,
        "AppIcon-76x76@2x.png": 152,
        "AppIcon-83.5x83.5@2x.png": 167,
        "AppIcon-512@2x.png": 1024,
    }
    for name, size in ios_specs.items():
        write(size, ios_dir / name)

    # ── Android (mipmap-{density} = ic_launcher.png + ic_launcher_round.png + ic_launcher_foreground.png) ──
    android_specs = {
        "mdpi": 48,
        "hdpi": 72,
        "xhdpi": 96,
        "xxhdpi": 144,
        "xxxhdpi": 192,
    }
    for density, size in android_specs.items():
        d = ICONS / "android" / f"mipmap-{density}"
        write(size, d / "ic_launcher.png")
        write(size, d / "ic_launcher_round.png")
        # foreground 通常更大 (1.5x), 透明边框给 adaptive icon 留 padding
        write(int(size * 1.5), d / "ic_launcher_foreground.png")

    print("\n✓ 全套 icon 渲染完毕")


if __name__ == "__main__":
    main()
