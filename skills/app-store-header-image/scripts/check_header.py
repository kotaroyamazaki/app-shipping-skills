#!/usr/bin/env python3
"""Check that a header / search-results image matches the App Store Asset Library specs,
and optionally fix it.

Usage:
  python3 check_header.py IN.png                 # check only (Python standard library, PNG/JPEG)
  python3 check_header.py IN.png --fix OUT.png   # flatten transparency and center-crop a few
                                                 # stray pixels (needs Pillow: pip install pillow)

Sizes are the `asc asset-library specs` values as of 2026-10. If they changed, trust
`asc asset-library specs --output table` over this file.

Exit code: 0 = OK, 1 = problems found (or could not fix), 2 = unreadable input.
"""
import argparse
import struct
import sys

SPECS = {
    (3840, 1646): "header only, 21:9 (PRODUCT_PAGE_HEADER_ASSET)",
    (5244, 2950): "header + search results, 16:9 (both placements)",
}
SEARCH_3_2 = ((1920, 1280), (3840, 2560))  # search-results-only 3:2, min and max

# PNG color types: 0 gray, 2 RGB, 3 palette, 4 gray+alpha, 6 RGBA
PNG_COLOR = {0: "L", 2: "RGB", 3: "P", 4: "LA", 6: "RGBA"}


def kind(size):
    if size in SPECS:
        return SPECS[size]
    w, h = size
    (lw, lh), (hw, hh) = SEARCH_3_2
    if lw <= w <= hw and lh <= h <= hh and w * 2 == h * 3:
        return "search results only, 3:2 (APP_STORE_SEARCH_RESULTS_ASSET)"
    return None


def read_png(f):
    """Return (width, height, mode, has_trns)."""
    f.seek(8)
    width = height = None
    mode = None
    has_trns = False
    while True:
        head = f.read(8)
        if len(head) < 8:
            break
        length, ctype = struct.unpack(">I4s", head)
        if ctype == b"IHDR":
            data = f.read(length)
            width, height, _depth, color = struct.unpack(">IIBB", data[:10])
            mode = PNG_COLOR.get(color, f"color-type-{color}")
            f.seek(4, 1)
            continue
        if ctype == b"tRNS":
            has_trns = True
        if ctype in (b"IDAT", b"IEND"):
            break
        f.seek(length + 4, 1)
    if width is None:
        raise ValueError("PNG without IHDR")
    return width, height, mode, has_trns


def read_jpeg(f):
    """Return (width, height, mode, False). JPEG has no alpha channel."""
    f.seek(2)
    while True:
        b = f.read(1)
        while b and b != b"\xff":
            b = f.read(1)
        while b == b"\xff":
            b = f.read(1)
        if not b:
            raise ValueError("JPEG without SOF marker")
        marker = b[0]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        (length,) = struct.unpack(">H", f.read(2))
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            _p, height, width, comps = struct.unpack(">BHHB", f.read(6))
            return width, height, {1: "L", 3: "RGB", 4: "CMYK"}.get(comps, f"{comps}ch"), False
        f.seek(length - 2, 1)


def inspect(path):
    with open(path, "rb") as f:
        sig = f.read(8)
        if sig.startswith(b"\x89PNG\r\n\x1a\n"):
            return read_png(f)
        if sig.startswith(b"\xff\xd8"):
            return read_jpeg(f)
    raise ValueError("not a PNG or JPEG file")


def fix(src, dst):
    try:
        from PIL import Image
    except ImportError:
        sys.exit("--fix needs Pillow: pip install pillow")
    im = Image.open(src).convert("RGBA")
    flat = Image.new("RGB", im.size, (0, 0, 0))
    flat.paste(im, (0, 0), im)
    if not kind(flat.size):
        for (w, h) in SPECS:
            dw, dh = flat.size[0] - w, flat.size[1] - h
            if 0 <= dw <= 32 and 0 <= dh <= 32:  # trim only the leftover from generation sizes
                flat = flat.crop((dw // 2, dh // 2, dw // 2 + w, dh // 2 + h))
                break
        else:
            print("NG: size is too far from any spec to fix automatically; re-export at the right size")
            sys.exit(1)
    flat.save(dst)
    print(f"saved {dst}: {flat.size[0]}x{flat.size[1]} RGB")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("--fix", metavar="OUT", help="write a fixed RGB copy here")
    a = ap.parse_args()
    try:
        w, h, mode, has_trns = inspect(a.src)
    except (OSError, ValueError, struct.error) as e:
        print(f"{a.src}: cannot read ({e})")
        sys.exit(2)
    problems = []
    if mode not in ("RGB", "L") or has_trns:
        problems.append(
            f"color mode is {mode}{' with tRNS transparency' if has_trns else ''} "
            "(any alpha channel is rejected, even if every pixel is opaque)"
        )
    k = kind((w, h))
    if not k:
        problems.append(f"size {w}x{h} matches no spec")
    print(f"{a.src}: {w}x{h} {mode} -> {k or 'no matching spec'}")
    if not problems:
        print("OK")
        return
    for p in problems:
        print("NG:", p)
    if not a.fix:
        sys.exit(1)
    fix(a.src, a.fix)


if __name__ == "__main__":
    main()
