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


def _ascii85decode(data):
    """Decode the PDF ASCII85 variant (whitespace and z shorthand)."""
    data = re.sub(rb"\s+", b"", data)
    if data.startswith(b"<~"):
        data = data[2:]
    if b"~>" in data:
        data = data[:data.find(b"~>")]
    out, group = bytearray(), []
    for value in data:
        if value == ord("z") and not group:
            out.extend(b"\0\0\0\0")
            continue
        if 33 <= value <= 117:
            group.append(value - 33)
        if len(group) == 5:
            number = 0
            for digit in group:
                number = number * 85 + digit
            out.extend(number.to_bytes(4, "big")); group = []
    if group:
        original = len(group)
        group.extend([84] * (5 - original))
        number = 0
        for digit in group:
            number = number * 85 + digit
        out.extend(number.to_bytes(4, "big")[:original - 1])
    return bytes(out)


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
            header = body[:sm.start()]
            # Take the stream by its declared /Length, not by hunting for the
            # next "endstream".  A COMPRESSED stream is arbitrary bytes and can
            # contain "endobj" or "endstream" by chance; when it does, the old
            # slice cut the data short, zlib refused it, and the whole PAGE
            # dropped out of the extraction silently.  Measured 2026-09-04 on
            # b02-part-drawings.pdf: 11 pages in the file, 10 in the output,
            # and the missing page held four real parts.  Fallback kept for
            # streams that declare no length.
            length = re.search(rb"/Length\s+(\d+)\b", header)
            if length:
                s0 = start + sm.end()
                raw = data[s0:s0 + int(length.group(1))]
            else:
                se = body.find(b"endstream", sm.end())
                raw = body[sm.end():se if se > 0 else len(body)].rstrip(b"\r\n")
            stream = raw
            filters = re.findall(rb"/(ASCII85Decode|A85|FlateDecode|Fl)",
                                 header)
            try:
                for stream_filter in filters:
                    if stream_filter in (b"ASCII85Decode", b"A85"):
                        stream = _ascii85decode(stream)
                    else:
                        stream = zlib.decompress(stream)
            except (ValueError, OverflowError, zlib.error):
                stream = raw
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


def _dict_after(data, key):
    """Return the (possibly indirect) dictionary following key."""
    m = re.search(re.escape(key) + rb"\s*(?:(\d+)\s+\d+\s+R|<<)", data)
    if not m:
        return None, None
    if m.group(1):
        return int(m.group(1)), None
    start, pos, depth = m.end() - 2, m.end(), 1
    while pos < len(data) - 1:
        pair = data[pos:pos + 2]
        if pair == b"<<":
            depth += 1; pos += 2
        elif pair == b">>":
            depth -= 1; pos += 2
            if not depth:
                return None, data[start:pos]
        else:
            pos += 1
    return None, None


def _resource_dict(header, objs):
    ref, inline = _dict_after(header, b"/Resources")
    if ref is not None:
        return objs.get(ref, (b"", None))[0]
    return inline


def _font_cmaps(resources, objs):
    """Return the font-name -> CMap mapping for one resource dictionary."""
    if not resources:
        return {}
    ref, font_dict = _dict_after(resources, b"/Font")
    if ref is not None:
        font_dict = objs.get(ref, (b"", None))[0]
    if not font_dict:
        return {}
    result = {}
    for name, font_ref in re.findall(
            rb"/([A-Za-z0-9_.-]+)\s+(\d+)\s+\d+\s+R", font_dict):
        font = objs.get(int(font_ref))
        if not font:
            continue
        cmref = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", font[0])
        if not cmref:
            continue
        cmap_stream = objs.get(int(cmref.group(1)), (None, None))[1]
        if cmap_stream:
            result[name.decode()] = _parse_cmap(cmap_stream)
    return result


def _xobject_refs(resources, objs):
    """Return named Form XObjects from one resource dictionary."""
    if not resources:
        return {}
    ref, xobjects = _dict_after(resources, b"/XObject")
    if ref is not None:
        xobjects = objs.get(ref, (b"", None))[0]
    if not xobjects:
        return {}
    return {name.decode(): int(num) for name, num in re.findall(
        rb"/([A-Za-z0-9_.-]+)\s+(\d+)\s+\d+\s+R", xobjects)}


def _parse_cmap(c):
    table = {}
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", c, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            table[bytes.fromhex(src.decode())] = _hex_to_str(dst)
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", c, re.S):
        for lo, hi, dst in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            src_len = len(lo) // 2
            dst_bytes = bytes.fromhex(dst.decode())
            base = int.from_bytes(dst_bytes, "big")
            for i in range(lo_i, min(hi_i, lo_i + 65535) + 1):
                value = (base + i - lo_i).to_bytes(len(dst_bytes), "big")
                table[i.to_bytes(src_len, "big")] = _hex_to_str(value.hex())
        for lo, hi, values in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", blk,
                re.S):
            lo_i, hi_i, src_len = int(lo, 16), int(hi, 16), len(lo) // 2
            dests = re.findall(rb"<([0-9A-Fa-f]+)>", values)
            for offset, dst in enumerate(dests[:hi_i - lo_i + 1]):
                table[(lo_i + offset).to_bytes(src_len, "big")] = _hex_to_str(dst)
    return table


def _streams(data):
    return [s for _h, s in _objects(data).values() if s]


def _content_streams(objs):
    """Return (page content stream, page font maps) pairs."""
    chunks = []
    for header, _stream in objs.values():
        if not re.search(rb"/Type\s*/Page\b", header):
            continue
        resources = _resource_dict(header, objs)
        ancestor = header
        seen = set()
        while resources is None:
            parent = re.search(rb"/Parent\s+(\d+)\s+\d+\s+R", ancestor)
            if not parent or int(parent.group(1)) in seen:
                break
            parent_num = int(parent.group(1)); seen.add(parent_num)
            ancestor = objs.get(parent_num, (b"", None))[0]
            resources = _resource_dict(ancestor, objs)
        fonts = _font_cmaps(resources, objs)
        match = re.search(
            rb"/Contents\s*(?:\[([^]]*)\]|(\d+)\s+\d+\s+R)", header, re.S)
        if not match:
            continue
        if match.group(2):
            refs = [int(match.group(2))]
        else:
            refs = [int(n) for n in re.findall(rb"(\d+)\s+\d+\s+R",
                                                match.group(1))]
        page_chunks = [(objs[num][1], fonts) for num in refs
                       if num in objs and objs[num][1]]
        chunks.extend(page_chunks)

        # Some producers put all page text in Form XObjects.  Walk only forms
        # actually invoked by the page/form stream and give each its own
        # resources (or the caller's resources when omitted).
        pending = [(stream, resources) for stream, _fonts in page_chunks]
        visited = set()
        while pending:
            caller_stream, caller_resources = pending.pop(0)
            xobjects = _xobject_refs(caller_resources, objs)
            invoked = re.findall(rb"/([A-Za-z0-9_.-]+)\s+Do\b", caller_stream)
            for name in invoked:
                obj_num = xobjects.get(name.decode())
                if obj_num is None or obj_num in visited:
                    continue
                form = objs.get(obj_num)
                if not form or not form[1] or not re.search(
                        rb"/Subtype\s*/Form\b", form[0]):
                    continue
                visited.add(obj_num)
                form_resources = _resource_dict(form[0], objs) or caller_resources
                chunks.append((form[1], _font_cmaps(form_resources, objs)))
                pending.append((form[1], form_resources))
    return chunks


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


def _decode_with_cmap(value, cmap):
    """Decode a PDF string using variable-width source codes."""
    if not cmap:
        return value.decode("latin-1", "replace")
    lengths = sorted({len(code) for code in cmap}, reverse=True)
    out, pos = [], 0
    while pos < len(value):
        for size in lengths:
            code = value[pos:pos + size]
            if len(code) == size and code in cmap:
                out.append(cmap[code]); pos += size
                break
        else:
            # Preserve the old fallback for an unmapped byte rather than
            # dropping an entire string when a font has an incomplete CMap.
            out.append(bytes([value[pos]]).decode("latin-1")); pos += 1
    return "".join(out)


def _tj_adjustment_is_space(value):
    """Return whether a TJ positioning adjustment represents a word gap."""
    # In this corpus, ordinary negative kerning adjustments top out at 21
    # thousandths of an em, while spacing adjustments begin above 100.  Keep
    # the cutoff at that observed boundary rather than splitting on every
    # adjustment (which turns individually positioned glyphs into words).
    return -value > 100


def extract(path, raw=False):
    data = open(path, "rb").read()
    objs = _objects(data)
    chunks = _content_streams(objs)
    cmap = {}
    lines = []
    for c, fonts in chunks:
        if b"BT" not in c:
            continue
        text = c.decode("latin-1", "replace")
        cur = []
        in_tj_array = False
        for m in re.finditer(r"%[^\r\n]*"
                             r"|/([A-Za-z0-9_.-]+)\s+[\d.]+\s+Tf"
                             r"|([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+(Td|TD)"
                             r"|\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>"
                             r"|([\[\]])|([-+]?\d*\.?\d+)"
                             r"|\bTJ\b|\bTj\b|\bTd\b|\bTD\b|\bT\*\b|\bET\b",
                             text):
            tok = m.group()
            if tok.startswith("%"):           # comments may contain (parens)
                continue
            if m.group(1):                      # font switch
                cmap = fonts.get(m.group(1), {})
                continue
            if m.group(4):                      # positioned text move
                # A zero vertical move merely advances to the next glyph.
                # The affected PDFs use one Tj per glyph with "x 0 Td" moves.
                if float(m.group(3)) and cur:
                    lines.append("".join(cur)); cur = []
                continue
            if m.group(5):
                in_tj_array = tok == "["
                continue
            if m.group(6):
                if (in_tj_array and _tj_adjustment_is_space(float(tok))
                        and cur and not cur[-1].endswith(" ")):
                    cur.append(" ")
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
                    cur.append(_decode_with_cmap(bytes.fromhex(hx), cmap))
                else:
                    cur.append(_hex_to_str(hx))
            else:
                b = _unescape(tok[1:-1].encode("latin-1", "replace"))
                cur.append(_decode_with_cmap(b, cmap))
        if cur:
            lines.append("".join(cur))
    result = "\n".join(lines) if raw else "\n".join(
        l.strip() for l in lines if l.strip())
    # NUL bytes must never reach the caller. Measured 2026-08-25 on the owner's
    # own "12-Month Income Plan" PDF: the extraction was complete (26,774 chars)
    # but carried 39 embedded NULs, the first at offset 125. Stored in SQLite
    # that text is intact, yet `length()` reports 125 and -- far worse -- `LIKE`
    # matches NOTHING past the first NUL. The document was silently unsearchable
    # in the idea box while looking merely "short" in every diagnostic.
    # They are decoding artifacts (unmapped glyphs), never meaningful content.
    result = result.replace("\x00", "")
    return result if _plausible_text(result) else ""


def _plausible_text(text):
    """Reject binary data accidentally interpreted as text."""
    if sum(ch.isalpha() for ch in text) < 3:
        return False
    acceptable = sum(ch.isprintable() or ch in "\n\r\t" for ch in text)
    return acceptable / max(len(text), 1) > 0.85


def main(argv):
    # `--help` used to fall through to open(argv[1]) and die with a traceback
    # naming "--help" as a missing file. The usage text was always here; only
    # the flag that everyone tries first was not wired to it.
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2 if len(argv) < 2 else 0
    txt = extract(argv[1], raw="--raw" in argv)
    if not txt.strip():
        return 0
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
