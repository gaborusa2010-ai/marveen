#!/usr/bin/env python3
"""
Minimal dependency-free PDF writer for workshop cut diagrams.

Nothing on a bare Linux box converts SVG to a printable page, and a carpenter
wants paper on the bench, not a file on a phone. So this writes PDF directly:
vector rectangles, real selectable text, one A4 landscape page per sheet.

Only the PDF operators needed for this job are implemented. It is not a general
PDF library and does not try to be.
"""
import zlib

A4_LANDSCAPE = (842.0, 595.0)
A4_PORTRAIT = (595.0, 842.0)

# Helvetica's built-in encoding is WinAnsi, which has no o-double-acute or
# u-double-acute. Hungarian text would render as garbage, so fold those two to
# their single-acute neighbours rather than emit a broken glyph.
_FOLD = {"ő": "ö", "Ű": "Ö", "ű": "ü", "Ő": "Ü"}


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


class Page:
    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.ops = []

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

    def text(self, x, y, s, size=9, colour=(0, 0, 0), align="left", font="F1"):
        # Helvetica average advance is close enough to 0.5 em for centring
        # labels inside a box; this is a diagram, not typesetting.
        if align == "center":
            x -= 0.25 * size * len(str(s))
        elif align == "right":
            x -= 0.5 * size * len(str(s))
        self.ops.append("BT /%s %.1f Tf %.3f %.3f %.3f rg %.2f %.2f Td (%s) Tj ET"
                        % (font, size, colour[0], colour[1], colour[2], x, y, _enc(s)))

    def line(self, x1, y1, x2, y2, colour=(0, 0, 0), lw=0.5, dash=None):
        self.ops.append("%.3f %.3f %.3f RG %.2f w" % (colour + (lw,)))
        self.ops.append("[%s] 0 d" % ("" if not dash else " ".join(str(d) for d in dash)))
        self.ops.append("%.2f %.2f m %.2f %.2f l S" % (x1, y1, x2, y2))
        self.ops.append("[] 0 d")

    def stream(self):
        return "\n".join(self.ops).encode("latin-1", "replace")


def write_pdf(path, pages, title="", compress=True):
    objs = []          # 1-indexed object bodies as bytes

    def add(body):
        objs.append(body)
        return len(objs)

    font_regular = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                       b"/Encoding /WinAnsiEncoding >>")
    font_bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                    b"/Encoding /WinAnsiEncoding >>")

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
        pid = add(("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
                   "/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> "
                   "/Contents %d 0 R >>"
                   % (pages_obj_id, pg.w, pg.h, font_regular, font_bold, content_id)
                   ).encode())
        page_ids.append(pid)

    kids = " ".join("%d 0 R" % i for i in page_ids)
    objs[pages_obj_id - 1] = ("<< /Type /Pages /Kids [%s] /Count %d >>"
                              % (kids, len(page_ids))).encode()
    catalog_id = add(("<< /Type /Catalog /Pages %d 0 R >>" % pages_obj_id).encode())
    info_id = add(("<< /Title (%s) /Producer (marveen-muhely) >>" % _enc(title)).encode())

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
