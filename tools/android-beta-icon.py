#!/usr/bin/env python3
"""Derive Android launcher layers from Hozz's existing Apple icon master."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path("App/Assets.xcassets/AppIcon.appiconset/icon-1024.png")
RESOURCES = Path("Android/app/src/main/res/drawable-nodpi")
PROVENANCE = Path("Android/launcher-icon-provenance.json")
GENERATOR = Path("tools/android-beta-icon.py")


def sha256(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def check():
    metadata = json.loads((ROOT / PROVENANCE).read_text())
    if metadata["sourceSha256"] != sha256(SOURCE) or metadata["generatorSha256"] != sha256(GENERATOR):
        raise ValueError("Launcher source/generator changed; regenerate Android launcher layers.")
    for name, digest in metadata["generatedSha256"].items():
        if sha256(Path(name)) != digest:
            raise ValueError("Generated Android launcher layer changed; regenerate it.")
    print("Android launcher layers match the existing Hozz master and generator.")


def generate():
    # Pillow is used only when regenerating committed assets, not by Gradle/CI's --check.
    from PIL import Image

    master = Image.open(ROOT / SOURCE).convert("RGBA")
    if master.size != (1024, 1024):
        raise ValueError("Expected the existing 1024-square Hozz icon master.")
    pixels = master.load()
    background = Image.new("RGBA", (1, master.height))
    foreground = Image.new("RGBA", master.size)
    monochrome = Image.new("RGBA", master.size)
    front_pixels = foreground.load()
    mono_pixels = monochrome.load()
    ink_count = 0
    for y in range(master.height):
        edge = pixels[0, y]
        if edge != pixels[master.width - 1, y] or edge[3] != 255:
            raise ValueError("Master background changed; review layer extraction instead of guessing.")
        background.putpixel((0, y), edge)
        for x in range(master.width):
            pixel = pixels[x, y]
            if pixel != edge:
                front_pixels[x, y] = pixel
                # Android's themed icon uses the existing light pixel-art shapes;
                # the dark eyes, mouth and ring interior stay transparent.
                if max(pixel[:3]) >= 96:
                    mono_pixels[x, y] = (255, 255, 255, pixel[3])
                    ink_count += 1
                    radius_squared = (x + 0.5 - 512) ** 2 + (y + 0.5 - 512) ** 2
                    if radius_squared * (108 * 0.64 / 1024) ** 2 > 33 ** 2:
                        raise ValueError("Brand artwork falls outside the adaptive icon safe circle.")
    if ink_count < 100_000:
        raise ValueError("Unexpectedly empty Hozz mark; review the master before generating.")
    output = ROOT / RESOURCES
    output.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, image in [
        ("hozz_launcher_background.png", background),
        ("hozz_launcher_foreground_image.png", foreground),
        ("hozz_launcher_monochrome_image.png", monochrome),
    ]:
        relative = RESOURCES / name
        image.save(ROOT / relative, format="PNG", optimize=False, compress_level=9)
        files[str(relative)] = sha256(relative)
    metadata = {
        "source": str(SOURCE),
        "sourceSha256": sha256(SOURCE),
        "generator": str(GENERATOR),
        "generatorSha256": sha256(GENERATOR),
        "license": "GPL-3.0-only; see repository LICENSE (Hozz contributors)",
        "method": "Preserve original RGB pixels; separate row-uniform background; no AI generation.",
        "foregroundInsetPercent": 18,
        "monochromeLightPixelThreshold": 96,
        "generatedSha256": files,
    }
    (ROOT / PROVENANCE).write_text(json.dumps(metadata, indent=2) + "\n")
    check()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify committed assets; standard library only.")
    args = parser.parse_args()
    try:
        check() if args.check else generate()
    except (OSError, ValueError, KeyError, ImportError) as error:
        print("Android icon: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
