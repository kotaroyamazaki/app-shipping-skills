#!/usr/bin/env python3
"""Example: compose a 3840x1646 App Store header image from your app's own assets.

This is a starting point to adapt, not a one-size-fits-all generator. It shows the
techniques that worked in practice:
  - tile an in-app background texture across the wide canvas and darken it
  - add a warm spotlight + vignette so the hero object stands out
  - place the hero object with a soft drop shadow
  - overlay the app's real logo (SVG rendered with rsvg-convert so it stays sharp)
  - optional brush stroke under the logo, tinted and with its edge re-hardened after upscaling

Usage:
  python3 compose_example.py INPUT_DIR [--out header.png] [--preview preview.jpg]

INPUT_DIR is expected to contain (any of .png/.webp/.jpg unless noted):
  background.*   required  a texture/background from the app; tiled horizontally
  hero.*         optional  main object (transparent PNG/WebP), placed right of center
  title.svg      optional  the app's logo as SVG (needs `rsvg-convert`, from librsvg)
  title.png      optional  used if there is no title.svg
  brush.*        optional  a white brush-stroke with alpha, drawn under the title

Requires Pillow (pip install pillow). The output is RGB with no alpha, as App Store
requires. Always run check_header.py on the result and show it to the user before upload.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter
except ImportError:
    sys.exit("compose_example.py needs Pillow: pip install pillow")

W, H = 3840, 1646
EXTS = (".png", ".webp", ".jpg", ".jpeg")


def find(d, stem, required=False):
    for ext in EXTS:
        p = d / f"{stem}{ext}"
        if p.exists():
            return p
    if required:
        sys.exit(f"missing {stem}.png/.webp/.jpg in {d}")
    return None


def tint(im, rgb, alpha=1.0, harden=None):
    """Recolor a white mark (brush stroke, ink splash) to one color.
    harden=(lo, hi) re-levels the alpha so an upscaled edge becomes crisp again."""
    a = im.split()[3]
    if harden:
        lo, hi = harden
        a = a.point(lambda v: max(0, min(255, int((v - lo) * 255 / (hi - lo)))))
    a = a.point(lambda v: int(v * alpha))
    out = Image.new("RGBA", im.size, rgb + (255,))
    out.putalpha(a)
    return out


def shadow(layer, offset, blur, strength=1.0):
    a = layer.split()[3].point(lambda v: int(v * strength))
    s = Image.new("RGBA", layer.size, (0, 0, 0, 255))
    s.putalpha(a)
    return ImageChops.offset(s, *offset).filter(ImageFilter.GaussianBlur(blur))


def place(canvas, im, cx, cy, sh=None):
    """Paste im centered at (cx, cy); sh=((dx, dy), blur, strength) adds a drop shadow."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(im, (int(cx - im.size[0] / 2), int(cy - im.size[1] / 2)), im)
    if sh:
        canvas.alpha_composite(shadow(layer, *sh))
    canvas.alpha_composite(layer)


def tiled_background(path, darken=0.62):
    tex = Image.open(path).convert("RGB")
    tw = int(tex.size[0] * H / tex.size[1])
    tex = tex.resize((tw, H), Image.LANCZOS)
    bg = Image.new("RGB", (W, H))
    x, i = 0, 0
    while x < W:  # mirror every other tile so seams are less visible
        bg.paste(tex if i % 2 == 0 else tex.transpose(Image.FLIP_LEFT_RIGHT), (x, 0))
        x += tw
        i += 1
    return ImageEnhance.Brightness(bg).enhance(darken)


def spotlight(bg, cx, cy, rx, ry, warm=(255, 214, 160), strength=0.55):
    """Warm light around (cx, cy) plus a vignette, without numpy."""
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(min(rx, ry) // 2))
    lit = ImageEnhance.Brightness(bg).enhance(1.6)
    lit = ImageChops.multiply(lit, Image.new("RGB", (W, H), warm))
    bg = Image.composite(lit, bg, mask.point(lambda v: int(v * strength)))
    dark = ImageEnhance.Brightness(bg).enhance(0.45)
    return Image.composite(bg, dark, mask.point(lambda v: min(255, 90 + v)))


def render_title(d, width, tmp):
    svg = d / "title.svg"
    if svg.exists():
        if not shutil.which("rsvg-convert"):
            sys.exit("title.svg found but rsvg-convert is missing (brew install librsvg / apt install librsvg2-bin)")
        out = Path(tmp) / "title.png"
        subprocess.run(["rsvg-convert", "-w", str(width), str(svg), "-o", str(out)], check=True)
        return Image.open(out).convert("RGBA")
    png = find(d, "title")
    if png:
        t = Image.open(png).convert("RGBA")
        return t.resize((width, int(t.size[1] * width / t.size[0])), Image.LANCZOS)
    return None


def build(d, out, preview):
    hero_x, hero_y = 2790, 823   # keep important elements well away from the edges
    title_x, title_y = 1190, 830

    bg = tiled_background(find(d, "background", required=True))
    bg = spotlight(bg, hero_x, hero_y, 1500, 1100).convert("RGBA")

    brush = find(d, "brush")
    if brush:
        stroke = Image.open(brush).convert("RGBA").resize((2500, 620), Image.LANCZOS)
        stroke = stroke.filter(ImageFilter.GaussianBlur(2))
        stroke = tint(stroke, (8, 6, 6), 0.90, harden=(70, 190))
        place(bg, stroke.rotate(-3, expand=True, resample=Image.BICUBIC), title_x, title_y + 20)

    hero = find(d, "hero")
    if hero:
        im = Image.open(hero).convert("RGBA")
        hh = 1330
        im = im.resize((int(im.size[0] * hh / im.size[1]), hh), Image.LANCZOS)
        place(bg, im, hero_x, hero_y, sh=((30, 46), 46, 0.85))

    with tempfile.TemporaryDirectory() as tmp:
        title = render_title(d, 2000, tmp)
        if title:
            place(bg, title, title_x, title_y, sh=((8, 14), 16, 0.9))

    final = bg.convert("RGB")  # no alpha channel, ever
    final.save(out)
    print(f"saved {out}: {final.size[0]}x{final.size[1]} {final.mode}")
    if preview:
        final.resize((1920, 823), Image.LANCZOS).save(preview, quality=90)
        print(f"saved preview {preview}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_dir", type=Path)
    ap.add_argument("--out", default="header_3840x1646.png")
    ap.add_argument("--preview", help="also write a half-size JPEG preview here")
    a = ap.parse_args()
    if not a.input_dir.is_dir():
        sys.exit(f"not a directory: {a.input_dir}")
    build(a.input_dir, a.out, a.preview)


if __name__ == "__main__":
    main()
