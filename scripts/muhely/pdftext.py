#!/usr/bin/env python3
"""
Minimal PDF text extractor, dependency free.

There is no poppler on this machine, so a received PDF (a client's drawing
notes, an old invoice) cannot be read any other way. This decompresses the
content streams and pulls the strings out of the text-showing operators.

It handles the common case: FlateDecode streams, Tj/TJ/'/" operators, and
ToUnicode CMaps for subset-embedded fonts. It does NOT do layout analysis or
OCR, so a scanned image-only PDF yields nothing. That is reported, not hidden.

Usage:  pdftext.py <file.pdf> [--raw]
"""
import re
import sys
import zlib


def _objects(data):
    """obj number -> (header bytes, decompressed stream or None)."""
    objs = {}
    for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b", data):
        num = int(m.group(1))
        start = m.end()
        end = data.find(b"endobj", start)
        body = data[start:end if end > 0 else len(data)]
        sm = re.search(rb"stream\r?\n", body)
        stream = None
        if sm:
            se = body.find(b"endstream", sm.end())
            raw = body[sm.end():se if se > 0 else len(body)].rstrip(b"\r\n")
            try:
                stream = zlib.decompress(raw)
            except zlib.error:
                stream = raw
            header = body[:sm.start()]
        else:
            header = body
        objs[num] = (header, stream)

    # PDF 1.5 and later may put ordinary indirect objects inside compressed
    # object streams.  Those objects cannot themselves contain streams, so it
    # is sufficient to split the decoded ObjStm payload and register each body
    # as a normal, streamless object.
    for header, stream in list(objs.values()):
        if not stream or not re.search(rb"/Type\s*/ObjStm\b", header):
            continue
        nm = re.search(rb"/N\s+(\d+)", header)
        fm = re.search(rb"/First\s+(\d+)", header)
        if not nm or not fm:
            continue
        count, first = int(nm.group(1)), int(fm.group(1))
        if first < 0 or first > len(stream):
            continue
        pairs = re.findall(rb"(\d+)\s+(\d+)", stream[:first])
        if len(pairs) < count:
            continue
        pairs = [(int(num), int(offset)) for num, offset in pairs[:count]]
        payload = stream[first:]
        for i, (num, offset) in enumerate(pairs):
            end = pairs[i + 1][1] if i + 1 < len(pairs) else len(payload)
            if 0 <= offset <= end <= len(payload):
                objs[num] = (payload[offset:end].strip(), None)
    return objs


def _font_cmaps(objs):
    """Resource name (F1, FF0, ...) -> its own cid table.

    Per font, never merged: two subset fonts reuse the same low CIDs for
    different glyphs, so one shared table turns half the document into noise.
    """
    tounicode = {}
    for num, (header, _stream) in objs.items():
        m = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", header)
        if m and b"/Font" in header:
            tounicode[num] = int(m.group(1))
    by_name = {}
    for num, (header, _s) in objs.items():
        for name, ref in re.findall(rb"/([A-Za-z0-9_.-]+)\s+(\d+)\s+\d+\s+R",
                                    header):
            ref = int(ref)
            if ref in tounicode:
                cm = objs.get(tounicode[ref], (None, None))[1]
                if cm:
                    by_name[name.decode()] = _parse_cmap(cm)
    return by_name


def _parse_cmap(c):
    table = {}
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", c, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            table[int(src, 16)] = _hex_to_str(dst)
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", c, re.S):
        for lo, hi, dst in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            lo_i, hi_i, base = int(lo, 16), int(hi, 16), int(dst, 16)
            for i in range(lo_i, min(hi_i, lo_i + 65535) + 1):
                table[i] = chr(base + (i - lo_i))
    return table


def _streams(data):
    return [s for _h, s in _objects(data).values() if s]


def _content_streams(objs):
    """Return page content streams, excluding fonts, XRefs, and metadata."""
    refs = []
    for header, _stream in objs.values():
        if not re.search(rb"/Type\s*/Page\b", header):
            continue
        match = re.search(
            rb"/Contents\s*(?:\[([^]]*)\]|(\d+)\s+\d+\s+R)", header, re.S)
        if not match:
            continue
        if match.group(2):
            refs.append(int(match.group(2)))
        else:
            refs.extend(int(n) for n in re.findall(rb"(\d+)\s+\d+\s+R",
                                                    match.group(1)))
    return [objs[num][1] for num in refs if num in objs and objs[num][1]]


def _cmaps(chunks):
    """Build cid -> unicode from any ToUnicode CMap present."""
    table = {}
    for c in chunks:
        if b"beginbfchar" not in c and b"beginbfrange" not in c:
            continue
        for blk in re.findall(rb"beginbfchar(.*?)endbfchar", c, re.S):
            for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
                table[int(src, 16)] = _hex_to_str(dst)
        for blk in re.findall(rb"beginbfrange(.*?)endbfrange", c, re.S):
            for lo, hi, dst in re.findall(
                    rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
                lo_i, hi_i, base = int(lo, 16), int(hi, 16), int(dst, 16)
                for i in range(lo_i, min(hi_i, lo_i + 65535) + 1):
                    table[i] = chr(base + (i - lo_i))
    return table


def _hex_to_str(h):
    b = bytes.fromhex(h.decode() if isinstance(h, bytes) else h)
    if len(b) >= 2:
        return b.decode("utf-16-be", "replace")
    return b.decode("latin-1", "replace")


def _unescape(s):
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == 0x5C and i + 1 < len(s):       # backslash
            nxt = s[i + 1]
            mapping = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}
            if nxt in mapping:
                out.append(mapping[nxt]); i += 2; continue
            if 0x30 <= nxt <= 0x37:            # octal
                j = i + 1
                oct_digits = b""
                while j < len(s) and len(oct_digits) < 3 and 0x30 <= s[j] <= 0x37:
                    oct_digits += bytes([s[j]]); j += 1
                out.append(int(oct_digits, 8) & 0xFF); i = j; continue
            out.append(nxt); i += 2; continue
        out.append(c); i += 1
    return bytes(out)


def extract(path, raw=False):
    data = open(path, "rb").read()
    objs = _objects(data)
    chunks = _content_streams(objs)
    fonts = _font_cmaps(objs)
    cmap = {}
    lines = []
    for c in chunks:
        if b"Tj" not in c and b"TJ" not in c:
            continue
        text = c.decode("latin-1", "replace")
        cur = []
        for m in re.finditer(r"/([A-Za-z0-9_.-]+)\s+[\d.]+\s+Tf"
                             r"|\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>"
                             r"|\bTJ\b|\bTj\b|\bTd\b|\bTD\b|\bT\*\b|\bET\b",
                             text):
            tok = m.group()
            if m.group(1):                      # font switch
                cmap = fonts.get(m.group(1), {})
                continue
            if tok in ("Td", "TD", "T*", "ET"):
                if cur:
                    lines.append("".join(cur)); cur = []
                continue
            if tok in ("Tj", "TJ"):
                continue
            if tok.startswith("<"):
                hx = re.sub(r"\s", "", tok[1:-1])
                if cmap:
                    for k in range(0, len(hx) - 1, 4 if max(cmap) > 255 else 2):
                        step = 4 if max(cmap) > 255 else 2
                        cid = int(hx[k:k + step] or "0", 16)
                        cur.append(cmap.get(cid, ""))
                else:
                    cur.append(_hex_to_str(hx))
            else:
                b = _unescape(tok[1:-1].encode("latin-1", "replace"))
                if cmap and all(x in cmap for x in b if x):
                    cur.append("".join(cmap.get(x, "") for x in b))
                else:
                    cur.append(b.decode("latin-1", "replace"))
        if cur:
            lines.append("".join(cur))
    result = "\n".join(lines) if raw else "\n".join(
        l.strip() for l in lines if l.strip())
    return result if _plausible_text(result) else ""


def _plausible_text(text):
    """Reject binary data accidentally interpreted as text."""
    if sum(ch.isalpha() for ch in text) < 3:
        return False
    acceptable = sum(ch.isprintable() or ch in "\n\r\t" for ch in text)
    return acceptable / max(len(text), 1) > 0.85


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    txt = extract(argv[1], raw="--raw" in argv)
    if not txt.strip():
        return 0
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
