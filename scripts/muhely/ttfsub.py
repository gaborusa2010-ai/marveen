#!/usr/bin/env python3
"""
Minimal TrueType reader and subsetter, dependency-free.

Why this exists: pdfout.py used Helvetica, one of the PDF base-14 fonts, whose
WinAnsi encoding has no o-double-acute or u-double-acute. Hungarian text came
out with those two folded to o-umlaut and u-umlaut -- readable, but wrong, and
the owner noticed it on the first document that mattered (2026-09-02).

The fix is a real embedded font. This module reads a .ttf, reports glyph ids
and advance widths, and writes a subset containing only the glyphs a document
actually uses. Glyph ids are PRESERVED by the subset (unused glyphs are emptied
rather than renumbered), which is what lets the PDF side use /CIDToGIDMap
/Identity and write glyph ids straight into the content stream. No encoding
heuristics, no viewer guesswork.
"""
import struct

# Composite glyph flags, TrueType `glyf` spec.
_ARG_1_AND_2_ARE_WORDS = 0x0001
_WE_HAVE_A_SCALE = 0x0008
_MORE_COMPONENTS = 0x0020
_WE_HAVE_AN_X_AND_Y_SCALE = 0x0040
_WE_HAVE_A_TWO_BY_TWO = 0x0080

# Tables a CIDFontType2 needs. `cmap` is deliberately NOT embedded: with
# Identity-H the content stream carries glyph ids, so the viewer never needs a
# character-to-glyph map. `post` and `name` are dropped for the same reason.
_KEEP_TABLES = ("cvt ", "fpgm", "glyf", "head", "hhea", "hmtx", "loca",
                "maxp", "prep")


class TrueTypeFont:
    def __init__(self, path):
        self.path = path
        self.data = open(path, "rb").read()
        self.tables = {}
        num_tables = struct.unpack(">H", self.data[4:6])[0]
        for i in range(num_tables):
            rec = 12 + 16 * i
            tag = self.data[rec:rec + 4].decode("latin-1")
            off, ln = struct.unpack(">II", self.data[rec + 8:rec + 16])
            self.tables[tag] = (off, ln)

        head = self._table("head")
        self.units_per_em = struct.unpack(">H", head[18:20])[0]
        self.bbox = struct.unpack(">hhhh", head[36:44])
        self.index_to_loc = struct.unpack(">h", head[50:52])[0]

        hhea = self._table("hhea")
        self.ascent, self.descent = struct.unpack(">hh", hhea[4:8])
        num_h_metrics = struct.unpack(">H", hhea[34:36])[0]

        self.num_glyphs = struct.unpack(">H", self._table("maxp")[4:6])[0]
        self._advances = self._read_hmtx(num_h_metrics)
        self._loca = self._read_loca()
        self._cmap = self._read_cmap()

    # -- table access -------------------------------------------------------
    def _table(self, tag):
        if tag not in self.tables:
            raise ValueError("%s: missing required table %r" % (self.path, tag))
        off, ln = self.tables[tag]
        return self.data[off:off + ln]

    def _read_hmtx(self, num_h_metrics):
        raw = self._table("hmtx")
        adv = []
        for i in range(num_h_metrics):
            adv.append(struct.unpack(">H", raw[4 * i:4 * i + 2])[0])
        # Glyphs past numberOfHMetrics all share the last advance width.
        last = adv[-1] if adv else 0
        while len(adv) < self.num_glyphs:
            adv.append(last)
        return adv

    def _read_loca(self):
        raw = self._table("loca")
        n = self.num_glyphs + 1
        if self.index_to_loc == 0:
            vals = struct.unpack(">%dH" % n, raw[:2 * n])
            return [v * 2 for v in vals]
        return list(struct.unpack(">%dI" % n, raw[:4 * n]))

    def _read_cmap(self):
        """Unicode -> glyph id, from the (3,1) or (3,10) subtable."""
        raw = self._table("cmap")
        n = struct.unpack(">H", raw[2:4])[0]
        best = None
        for i in range(n):
            pid, eid, off = struct.unpack(">HHI", raw[4 + 8 * i:12 + 8 * i])
            if (pid, eid) in ((3, 10), (3, 1), (0, 3), (0, 4)):
                fmt = struct.unpack(">H", raw[off:off + 2])[0]
                if fmt in (4, 12):
                    # Prefer format 12 (full Unicode) when both are present.
                    if best is None or fmt == 12:
                        best = (fmt, off)
        if best is None:
            raise ValueError("%s: no usable Unicode cmap subtable" % self.path)
        fmt, off = best
        return self._cmap4(raw, off) if fmt == 4 else self._cmap12(raw, off)

    def _cmap4(self, raw, off):
        seg_x2 = struct.unpack(">H", raw[off + 6:off + 8])[0]
        seg = seg_x2 // 2
        ends = struct.unpack(">%dH" % seg, raw[off + 14:off + 14 + seg_x2])
        s = off + 16 + seg_x2
        starts = struct.unpack(">%dH" % seg, raw[s:s + seg_x2])
        d = s + seg_x2
        deltas = struct.unpack(">%dh" % seg, raw[d:d + seg_x2])
        r = d + seg_x2
        ranges = struct.unpack(">%dH" % seg, raw[r:r + seg_x2])
        out = {}
        for i in range(seg):
            for code in range(starts[i], min(ends[i], 0xFFFF) + 1):
                if ranges[i] == 0:
                    gid = (code + deltas[i]) & 0xFFFF
                else:
                    gi = r + 2 * i + ranges[i] + 2 * (code - starts[i])
                    if gi + 2 > len(raw):
                        continue
                    gid = struct.unpack(">H", raw[gi:gi + 2])[0]
                    if gid:
                        gid = (gid + deltas[i]) & 0xFFFF
                if gid:
                    out[code] = gid
        return out

    def _cmap12(self, raw, off):
        n = struct.unpack(">I", raw[off + 12:off + 16])[0]
        out = {}
        for i in range(n):
            g = off + 16 + 12 * i
            start, end, gid = struct.unpack(">III", raw[g:g + 12])
            for code in range(start, min(end, 0x10FFFF) + 1):
                out[code] = gid + (code - start)
        return out

    # -- public queries -----------------------------------------------------
    def gid(self, ch):
        """Glyph id for a character, 0 (.notdef) if the font has no glyph."""
        return self._cmap.get(ord(ch), 0)

    def advance(self, gid):
        """Advance width in 1000-units-per-em space, the unit PDF wants."""
        if gid >= len(self._advances):
            return 0
        return int(round(self._advances[gid] * 1000.0 / self.units_per_em))

    def scaled(self, value):
        return int(round(value * 1000.0 / self.units_per_em))

    # -- subsetting ---------------------------------------------------------
    def _components(self, gid, seen):
        """Glyph ids a composite glyph references, recursively.

        Accented Latin letters in DejaVu are composites (base letter plus a
        floating accent), so forgetting this step yields a document where every
        accented character is blank. Found the hard way is one option; reading
        the glyf spec is cheaper.
        """
        if gid in seen or gid + 1 >= len(self._loca):
            return
        seen.add(gid)
        start, end = self._loca[gid], self._loca[gid + 1]
        if end <= start:
            return
        goff = self.tables["glyf"][0]
        g = self.data[goff + start:goff + end]
        if len(g) < 10 or struct.unpack(">h", g[:2])[0] >= 0:
            return                                  # simple glyph, no children
        p = 10
        while p + 4 <= len(g):
            flags, sub = struct.unpack(">HH", g[p:p + 4])
            p += 4
            p += 4 if flags & _ARG_1_AND_2_ARE_WORDS else 2
            if flags & _WE_HAVE_A_SCALE:
                p += 2
            elif flags & _WE_HAVE_AN_X_AND_Y_SCALE:
                p += 4
            elif flags & _WE_HAVE_A_TWO_BY_TWO:
                p += 8
            self._components(sub, seen)
            if not flags & _MORE_COMPONENTS:
                break

    def subset(self, gids):
        """A valid .ttf keeping only `gids`, with glyph ids unchanged.

        Unused glyphs become zero-length entries in `loca` instead of being
        renumbered. That keeps every id stable, so the PDF can declare
        /CIDToGIDMap /Identity and write ids directly.
        """
        keep = set()
        for g in set(gids) | {0}:
            self._components(g, keep)

        goff = self.tables["glyf"][0]
        new_glyf = bytearray()
        new_loca = []
        for gid in range(self.num_glyphs):
            new_loca.append(len(new_glyf))
            if gid in keep and gid + 1 < len(self._loca):
                start, end = self._loca[gid], self._loca[gid + 1]
                new_glyf += self.data[goff + start:goff + end]
                while len(new_glyf) % 4:            # glyf entries stay aligned
                    new_glyf += b"\0"
        new_loca.append(len(new_glyf))

        tables = {}
        for tag in _KEEP_TABLES:
            if tag in self.tables:
                tables[tag] = bytearray(self._table(tag))
        tables["glyf"] = bytes(new_glyf)
        # Always write the long loca format; the short one stores offsets
        # halved, which silently truncates once glyf passes 128 kB.
        tables["loca"] = struct.pack(">%dI" % len(new_loca), *new_loca)
        tables["head"][50:52] = struct.pack(">h", 1)
        tables["head"][8:12] = b"\0\0\0\0"          # checkSumAdjustment, set below
        return _build_sfnt(tables)


def _checksum(data):
    if len(data) % 4:
        data = data + b"\0" * (4 - len(data) % 4)
    total = 0
    for i in range(0, len(data), 4):
        total += struct.unpack(">I", data[i:i + 4])[0]
    return total & 0xFFFFFFFF


def _build_sfnt(tables):
    tags = sorted(tables)
    n = len(tags)
    # searchRange/entrySelector/rangeShift, per the sfnt header spec.
    sel = 0
    while (1 << (sel + 1)) <= n:
        sel += 1
    search = (1 << sel) * 16
    out = bytearray(struct.pack(">IHHHH", 0x00010000, n, search, sel,
                                n * 16 - search))
    offset = 12 + 16 * n
    records, body = bytearray(), bytearray()
    for tag in tags:
        data = bytes(tables[tag])
        records += (tag.encode("latin-1") +
                    struct.pack(">III", _checksum(data), offset, len(data)))
        body += data
        pad = (4 - len(data) % 4) % 4
        body += b"\0" * pad
        offset += len(data) + pad
    font = bytes(out + records + body)

    # checkSumAdjustment: 0xB1B0AFBA minus the checksum of the whole file.
    head_off = None
    for i, tag in enumerate(tags):
        if tag == "head":
            head_off = struct.unpack(">I", font[12 + 16 * i + 8:12 + 16 * i + 12])[0]
    if head_off is not None:
        adj = (0xB1B0AFBA - _checksum(font)) & 0xFFFFFFFF
        font = (font[:head_off + 8] + struct.pack(">I", adj) +
                font[head_off + 12:])
    return font
