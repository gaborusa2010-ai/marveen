#!/usr/bin/env python3
"""
Minimal dependency-free PDF writer for workshop cut diagrams.

Nothing on a bare Linux box converts SVG to a printable page, and a carpenter
wants paper on the bench, not a file on a phone. So this writes PDF directly:
vector rectangles, real selectable text, one A4 landscape page per sheet.

Only the PDF operators needed for this job are implemented. It is not a general
PDF library and does not try to be.
"""
import os
import zlib

A4_LANDSCAPE = (842.0, 595.0)
A4_PORTRAIT = (595.0, 842.0)

# Text is drawn with a real embedded TrueType font (DejaVu Sans) addressed by
# glyph id through /Identity-H. That is what makes o-double-acute and
# u-double-acute come out right.
#
# Until 2026-09-02 this used Helvetica, a PDF base-14 font whose WinAnsi
# encoding lacks those two letters, and folded them to o-umlaut and u-umlaut.
# The owner spotted it on the first document that went to him. The fold is gone;
# _FOLD survives only for the fallback path below.
_FOLD = {"ő": "ö", "Ű": "Ö", "ű": "ü", "Ő": "Ü"}

_FONT_CANDIDATES = {
    "F1": ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
           "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
           "/mnt/c/Windows/Fonts/arial.ttf",
           "/System/Library/Fonts/Supplemental/Arial.ttf",
           "/Library/Fonts/Arial.ttf"),
    "F2": ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
           "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
           "/mnt/c/Windows/Fonts/arialbd.ttf",
           "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
           "/Library/Fonts/Arial Bold.ttf"),
}
_FONT_CACHE = {}


def embedded_font(slot):
    """The TrueType face for slot F1/F2, or None to fall back to Helvetica.

    A missing font file must not take the whole PDF down: an invoice with
    folded accents still beats no invoice. Callers check for None.
    """
    if slot in _FONT_CACHE:
        return _FONT_CACHE[slot]
    face = None
    try:
        from ttfsub import TrueTypeFont
    except ImportError:
        try:
            import sys
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from ttfsub import TrueTypeFont
        except ImportError:
            TrueTypeFont = None
    if TrueTypeFont is not None:
        for path in _FONT_CANDIDATES.get(slot, ()):
            if os.path.exists(path):
                try:
                    face = TrueTypeFont(path)
                    break
                except Exception:
                    continue
    _FONT_CACHE[slot] = face
    return face


def _enc(text):
    out = []
    for ch in str(text):
        ch = _FOLD.get(ch, ch)
        if ch in "()\\":
            out.append("\\" + ch)
        elif ord(ch) < 32:
            out.append(" ")
        elif ord(ch) < 127:
            out.append(ch)
        else:
            try:
                out.append("\\%03o" % ch.encode("cp1252")[0])
            except (UnicodeEncodeError, IndexError):
                out.append("?")
    return "".join(out)


def _jpeg_size(data):
    """(width, height, components) from a JPEG's SOF marker.

    Walks the marker chain rather than trusting a fixed offset: JFIF/Exif
    headers vary in length, and a wrong guess silently yields a stretched page.
    """
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG (missing SOI marker)")
    i = 2
    while i < len(data) - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        # SOF0..SOF15 carry the frame header; C4 (DHT), C8, CC are not SOF.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h, data[i + 9]
        i += 2 + seg_len
    raise ValueError("no SOF marker found in JPEG")


class Page:
    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.ops = []
        self.images = []      # (name, jpeg_bytes, px_w, px_h, colourspace)
        self.used = {}        # font slot -> set of characters drawn on this page

    def image(self, path, x, y, w, h):
        """Place a JPEG at (x, y) with size w x h, in points.

        JPEG only, and deliberately so: PDF carries JPEG natively via
        /DCTDecode, so the file is embedded byte-for-byte with no decoding,
        no re-compression and no third-party library. PNG would need a full
        decoder here; convert it first instead, ffmpeg is on the box:
            ffmpeg -y -i page.png -q:v 2 page.jpg
        Added 2026-08-25: the owner's KDP colouring books need image pages,
        and this writer previously had no way to place one.
        """
        data = open(path, "rb").read()
        px_w, px_h, comps = _jpeg_size(data)
        cs = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(comps)
        if cs is None:
            raise ValueError("%s: %d-component JPEG is not supported" % (path, comps))
        name = "Im%d" % (len(self.images) + 1)
        self.images.append((name, data, px_w, px_h, cs))
        # cm maps the unit square onto the target rectangle; q/Q keeps it local.
        self.ops.append("q %.2f 0 0 %.2f %.2f %.2f cm /%s Do Q"
                        % (w, h, x, y, name))
        return px_w, px_h

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=0.6):
        if fill:
            self.ops.append("%.3f %.3f %.3f rg" % fill)
        if stroke:
            self.ops.append("%.3f %.3f %.3f RG" % stroke)
            self.ops.append("%.2f w" % lw)
        self.ops.append("%.2f %.2f %.2f %.2f re" % (x, y, w, h))
        if fill and stroke:
            self.ops.append("B")
        elif fill:
            self.ops.append("f")
        else:
            self.ops.append("S")

    def poly(self, pts, fill=None, stroke=None, lw=0.6, close=True):
        """Filled and/or stroked polygon from [(x, y), ...].

        `rect` only draws axis-aligned boxes, so an isometric face (a slanted
        parallelogram) could not be filled at all. Added 2026-09-04 for the
        part drawings, where each part is highlighted on a small 3D view of
        the carcass. Additive: nothing that used this module before changes.
        """
        if not pts:
            return
        if fill:
            self.ops.append("%.3f %.3f %.3f rg" % fill)
        if stroke:
            self.ops.append("%.3f %.3f %.3f RG" % stroke)
            self.ops.append("%.2f w" % lw)
        self.ops.append("%.2f %.2f m" % pts[0])
        for x, y in pts[1:]:
            self.ops.append("%.2f %.2f l" % (x, y))
        if close:
            self.ops.append("h")
        if fill and stroke:
            self.ops.append("B")
        elif fill:
            self.ops.append("f")
        else:
            self.ops.append("S")

    def text_width(self, s, size=9, font="F1"):
        """Width of `s` in points, from the font's real advance widths.

        With an embedded face this is exact. Without one it falls back to the
        old 0.5-em-per-character guess, which is what the Helvetica path always
        used and is good enough for diagram labels.
        """
        face = embedded_font(font)
        if face is None:
            return 0.5 * size * len(str(s))
        total = sum(face.advance(face.gid(ch)) for ch in str(s))
        return total * size / 1000.0

    def text(self, x, y, s, size=9, colour=(0, 0, 0), align="left", font="F1"):
        s = str(s)
        if align == "center":
            x -= self.text_width(s, size, font) / 2.0
        elif align == "right":
            x -= self.text_width(s, size, font)
        face = embedded_font(font)
        if face is None:
            body = "(%s) Tj" % _enc(s)
        else:
            gids = [face.gid(ch) for ch in s]
            self.used.setdefault(font, set()).update(s)
            body = "<%s> Tj" % "".join("%04X" % g for g in gids)
        self.ops.append("BT /%s %.1f Tf %.3f %.3f %.3f rg %.2f %.2f Td %s ET"
                        % (font, size, colour[0], colour[1], colour[2], x, y, body))

    def line(self, x1, y1, x2, y2, colour=(0, 0, 0), lw=0.5, dash=None):
        self.ops.append("%.3f %.3f %.3f RG %.2f w" % (colour + (lw,)))
        self.ops.append("[%s] 0 d" % ("" if not dash else " ".join(str(d) for d in dash)))
        self.ops.append("%.2f %.2f m %.2f %.2f l S" % (x1, y1, x2, y2))
        self.ops.append("[] 0 d")

    def stream(self):
        return "\n".join(self.ops).encode("latin-1", "replace")


def _tounicode_cmap(pairs):
    """A /ToUnicode CMap so the text stays selectable and searchable.

    Identity-H puts glyph ids in the content stream, which no reader can turn
    back into letters on its own. Without this the PDF looks right and copies
    out as gibberish -- and our own pdftext.py could not read it back either.
    """
    lines = ["/CIDInit /ProcSet findresource begin",
             "12 dict begin begincmap",
             "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) "
             "/Supplement 0 >> def",
             "/CMapName /Adobe-Identity-UCS def",
             "/CMapType 2 def",
             "1 begincodespacerange <0000> <FFFF> endcodespacerange"]
    pairs = sorted(pairs)
    for i in range(0, len(pairs), 100):
        chunk = pairs[i:i + 100]
        lines.append("%d beginbfchar" % len(chunk))
        for gid, ch in chunk:
            utf16 = ch.encode("utf-16-be")
            lines.append("<%04X> <%s>" % (gid, utf16.hex().upper()))
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop",
              "end", "end"]
    return "\n".join(lines).encode("latin-1")


def _emit_font(add, slot, chars, compress):
    """Build the object graph for one embedded font. Returns the /Type0 body.

    Four objects hang off it: the CIDFont, the descriptor, the subset font
    program and the ToUnicode map.
    """
    face = embedded_font(slot)
    name = "AAAAAA+" + os.path.basename(face.path).rsplit(".", 1)[0]
    gid_of = {ch: face.gid(ch) for ch in chars}
    gids = sorted(set(gid_of.values()) | {0})

    program = face.subset(gids)
    raw_len = len(program)
    if compress:
        program, extra = zlib.compress(program), b"/Filter /FlateDecode "
    else:
        extra = b""
    file_id = add(b"<< " + extra + b"/Length " + str(len(program)).encode() +
                  b" /Length1 " + str(raw_len).encode() + b" >>\nstream\n" +
                  program + b"\nendstream")

    desc_id = add(("<< /Type /FontDescriptor /FontName /%s /Flags 32 "
                   "/FontBBox [%d %d %d %d] /ItalicAngle 0 /Ascent %d "
                   "/Descent %d /CapHeight %d /StemV 80 /FontFile2 %d 0 R >>"
                   % (name, face.scaled(face.bbox[0]), face.scaled(face.bbox[1]),
                      face.scaled(face.bbox[2]), face.scaled(face.bbox[3]),
                      face.scaled(face.ascent), face.scaled(face.descent),
                      face.scaled(face.ascent), file_id)).encode())

    # /W lists a width for every glyph used; /DW covers anything else.
    widths = " ".join("%d [%d]" % (g, face.advance(g)) for g in gids if g)
    cid_id = add(("<< /Type /Font /Subtype /CIDFontType2 /BaseFont /%s "
                  "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) "
                  "/Supplement 0 >> /FontDescriptor %d 0 R /DW 1000 /W [%s] "
                  "/CIDToGIDMap /Identity >>" % (name, desc_id, widths)).encode())

    cmap = _tounicode_cmap([(gid_of[ch], ch) for ch in sorted(chars)])
    tou_id = add(b"<< /Length " + str(len(cmap)).encode() + b" >>\nstream\n" +
                 cmap + b"\nendstream")

    return ("<< /Type /Font /Subtype /Type0 /BaseFont /%s /Encoding /Identity-H "
            "/DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>"
            % (name, cid_id, tou_id)).encode()


def write_pdf(path, pages, title="", compress=True):
    objs = []          # 1-indexed object bodies as bytes

    def add(body):
        objs.append(body)
        return len(objs)

    # Reserved now, filled in after the pages are walked: the font subset can
    # only be built once every glyph the document uses is known.
    font_regular = add(b"")
    font_bold = add(b"")

    pages_obj_id = add(b"")            # placeholder, filled in below
    page_ids = []
    for pg in pages:
        raw = pg.stream()
        if compress:
            data = zlib.compress(raw)
            extra = b"/Filter /FlateDecode "
        else:
            data = raw
            extra = b""
        content_id = add(b"<< " + extra + b"/Length " + str(len(data)).encode() +
                         b" >>\nstream\n" + data + b"\nendstream")
        # Image XObjects, one per placed JPEG. The bytes go in verbatim under
        # /DCTDecode -- that is the whole point of restricting to JPEG.
        xobj = []
        for name, jpg, px_w, px_h, cs in getattr(pg, "images", []):
            img_id = add(("<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                          "/ColorSpace %s /BitsPerComponent 8 /Filter /DCTDecode "
                          "/Length %d >>\nstream\n" % (px_w, px_h, cs, len(jpg))
                          ).encode() + jpg + b"\nendstream")
            xobj.append("/%s %d 0 R" % (name, img_id))
        res = ("/Font << /F1 %d 0 R /F2 %d 0 R >>" % (font_regular, font_bold))
        if xobj:
            res += " /XObject << %s >>" % " ".join(xobj)
        pid = add(("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
                   "/Resources << %s >> /Contents %d 0 R >>"
                   % (pages_obj_id, pg.w, pg.h, res, content_id)
                   ).encode())
        page_ids.append(pid)

    for slot, top_id, fallback in (
            ("F1", font_regular, b"<< /Type /Font /Subtype /Type1 "
                                 b"/BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"),
            ("F2", font_bold, b"<< /Type /Font /Subtype /Type1 "
                              b"/BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")):
        chars = set()
        for pg in pages:
            chars |= getattr(pg, "used", {}).get(slot, set())
        if embedded_font(slot) is None or not chars:
            objs[top_id - 1] = fallback
        else:
            objs[top_id - 1] = _emit_font(add, slot, chars, compress)

    kids = " ".join("%d 0 R" % i for i in page_ids)
    objs[pages_obj_id - 1] = ("<< /Type /Pages /Kids [%s] /Count %d >>"
                              % (kids, len(page_ids))).encode()
    catalog_id = add(("<< /Type /Catalog /Pages %d 0 R >>" % pages_obj_id).encode())
    # A title with accents cannot ride in a plain PDF literal string; the
    # UTF-16BE form with a byte-order mark is the encoding readers agree on.
    if title and any(ord(c) > 126 for c in str(title)):
        title_obj = "<FEFF%s>" % str(title).encode("utf-16-be").hex().upper()
    else:
        title_obj = "(%s)" % _enc(title)
    info_id = add(("<< /Title %s /Producer (marveen-muhely) >>"
                   % title_obj).encode())

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n" % i).encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += ("xref\n0 %d\n" % (len(objs) + 1)).encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += ("%010d 00000 n \n" % off).encode()
    out += ("trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, catalog_id, info_id, xref_at)).encode()

    with open(path, "wb") as fh:
        fh.write(out)
    return path
