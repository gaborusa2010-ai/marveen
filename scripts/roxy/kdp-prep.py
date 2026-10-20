#!/usr/bin/env python3
"""
Make a generated picture printable: KDP trim size, 300 DPI, right format.

Why this exists, and why it is a separate step
----------------------------------------------
The image models return whatever pixel size they feel like (1024x1024 from
Leonardo, 1254x1254 from Codex) with no DPI in the header. KDP rejects an
interior page that is not the trim size at 300 DPI, and the rejection comes
after the upload, so the cost of getting it wrong is a whole round trip.

Roxy's own description claimed she did this with Pillow or ImageMagick. Neither
was installed (measured 2026-08-31), and this machine has no pip and no sudo, so
Pillow lives in a venv of its own: ~/.marveen-roxy-venv. RUN THIS WITH
THAT INTERPRETER, not with the system python3:

    ~/.marveen-roxy-venv/bin/python scripts/roxy/kdp-prep.py ...

What it does NOT do
-------------------
No CMYK conversion. KDP takes RGB for interiors and covers, and converting to
CMYK without the printer's profile makes the colours worse, not better. If a
book ever needs CMYK, that is a decision with a profile attached, not a flag.

Usage
-----
  kdp-prep.py --in FILE --out FILE [--trim 8.5x11] [--dpi 300] [--bleed]
              [--fit cover|contain] [--grayscale]

  --fit cover    (default) fill the page, crop the overflow. Right for art that
                 runs to the edge.
  --fit contain  fit the whole picture in, pad with white. Right for a colouring
                 page, where cropping would cut a line.
  --margin IN    keep this much white inside the trimmed edge (default 0 = off).

Why --margin exists
-------------------
Roxy found this in the Joshua Mayo walkthrough (2026-08-31): there the picture
fills the whole canvas with no bleed, and KDP let it through. That is luck, not a
rule -- it passed because the drawings happen to be white at the edge. A no-bleed
interior is expected to keep a margin from the trimmed edge, and our `contain`
output reaches exactly to the cut. Use --margin 0.25 for interiors.

Do NOT combine --margin with --bleed. A bleed page is meant to run past the cut;
a margin is the opposite instruction.
"""
import argparse
import os
import sys

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.stderr.write(
        "Nincs Pillow ebben az interpreterben.\n"
        "  Hasznald: ~/.marveen-roxy-venv/bin/python " + " ".join(sys.argv) + "\n")
    sys.exit(3)

# KDP-nel gyakori meretek hüvelykben. A kifestokonyv szokasos merete a 8.5x11.
TRIMS = {
    "8.5x11": (8.5, 11.0),
    "8x10": (8.0, 10.0),
    "6x9": (6.0, 9.0),
    "8.25x8.25": (8.25, 8.25),
}
BLEED_IN = 0.125  # KDP: 3.2 mm minden szabad elen


def parse_trim(s):
    if s in TRIMS:
        return TRIMS[s]
    try:
        w, h = s.lower().split("x")
        return float(w), float(h)
    except Exception:
        sys.stderr.write(f"Ismeretlen trim: {s}. Ismertek: {', '.join(TRIMS)} vagy SZELESSEGxMAGASSAG\n")
        sys.exit(2)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--trim", default="8.5x11")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--bleed", action="store_true", help="kifuto kep: +3.2mm minden elen")
    ap.add_argument("--margin", type=float, default=0.0,
                    help="feher sav a vagott szeltol, huvelykben (beliv: 0.25)")
    ap.add_argument("--fit", choices=("cover", "contain"), default="cover")
    ap.add_argument("--grayscale", action="store_true", help="szinezolaphoz: szurkearnyalatos")
    a = ap.parse_args(argv)

    win, hin = parse_trim(a.trim)
    if a.bleed:
        win += 2 * BLEED_IN
        hin += 2 * BLEED_IN
    if a.margin and a.bleed:
        sys.stderr.write("A --margin es a --bleed egymast zarja ki: a kifuto kep tullog a "
                         "vagason, a margo pont ellenkezoleg.\n")
        sys.exit(2)
    tw, th = round(win * a.dpi), round(hin * a.dpi)
    # A rajz a margon BELULI mezobe kerul; a lap merete valtozatlan marad.
    mpx = round(a.margin * a.dpi)
    iw, ih = tw - 2 * mpx, th - 2 * mpx
    if iw <= 0 or ih <= 0:
        sys.stderr.write(f"A {a.margin} huvelykes margo nagyobb, mint a lap.\n")
        sys.exit(2)

    im = Image.open(a.src)
    src_size = im.size
    im = im.convert("L" if a.grayscale else "RGB")

    if a.fit == "cover":
        # Kitolti a mezot, a tullogot levagja. ImageOps.fit kozepre igazit.
        im = ImageOps.fit(im, (iw, ih), method=Image.LANCZOS, centering=(0.5, 0.5))
        if mpx:
            canvas = Image.new(im.mode, (tw, th), 255 if a.grayscale else (255, 255, 255))
            canvas.paste(im, (mpx, mpx))
            im = canvas
        how = "kitoltve, a tullogo levagva"
    else:
        # Az EGESZ kep belefer, a maradek feher. Szinezolapnal ez a helyes:
        # egy levagott kontúr eltort vonal, nem muveszi dontes.
        im = ImageOps.contain(im, (iw, ih), method=Image.LANCZOS)
        canvas = Image.new(im.mode, (tw, th), 255 if a.grayscale else (255, 255, 255))
        canvas.paste(im, ((tw - im.width) // 2, (th - im.height) // 2))
        im = canvas
        how = "teljes egeszeben belefer, a maradek feher"

    os.makedirs(os.path.dirname(a.dst) or ".", exist_ok=True)
    ext = os.path.splitext(a.dst)[1].lower()
    save_kw = {"dpi": (a.dpi, a.dpi)}
    if ext in (".jpg", ".jpeg"):
        save_kw.update(quality=95, subsampling=0)
    im.save(a.dst, **save_kw)

    print(f"OK {a.dst}")
    print(f"   {src_size[0]}x{src_size[1]} -> {tw}x{th} px | {win:.3f}x{hin:.3f} in @ {a.dpi} DPI"
          f"{' + kifuto' if a.bleed else ''}")
    print(f"   {how}{', szurkearnyalatos' if a.grayscale else ''}"
          f"{f', {a.margin} huvelyk margo' if mpx else ''} | {os.path.getsize(a.dst)} bajt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
