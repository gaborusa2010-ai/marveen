#!/usr/bin/env python3
"""pdftext.py regressions (#644): indirect /Length and the encryption gate."""
import os
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f": {got!r} != {want!r}"))
    if not ok:
        FAILED.append(name)


def pdf(content, indirect=True, encrypt=None):
    stream = zlib.compress(content)
    length = b"/Length 5 0 R" if indirect else b"/Length %d" % len(stream)
    objs = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << >> /Contents 4 0 R >> endobj",
        b"4 0 obj << " + length + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream endobj",
        b"5 0 obj " + str(len(stream)).encode() + b" endobj",
    ]
    trailer = b"trailer << /Root 1 0 R /ID [<00112233445566778899aabbccddeeff><00>]"
    if encrypt:
        objs.append(b"6 0 obj " + encrypt + b" endobj")
        trailer += b" /Encrypt 6 0 R"
    return b"%PDF-1.4\n" + b"\n".join(objs) + b"\n" + trailer + b" >>\n%%EOF\n"


def run(data):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "pdftext.py"), f.name],
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout.strip(), r.stderr
    finally:
        os.unlink(f.name)


text = b"BT /F1 12 Tf 72 700 Td (Hello indirect length) Tj ET"
check("indirect /Length read", run(pdf(text))[:2], (0, "Hello indirect length"))
check("direct /Length still read", run(pdf(text, indirect=False))[:2], (0, "Hello indirect length"))

aes = (b"<< /Filter /Standard /V 4 /R 4 /O <" + b"00" * 32 + b"> /U <" + b"00" * 32
       + b"> /P -4 /CF << /StdCF << /CFM /AESV2 /Length 16 >> >> >>")
rc, out, err = run(pdf(text, encrypt=aes))
check("AES refused loudly", (rc, out, "AES" in err), (3, "", True))

rc4_bad = (b"<< /Filter /Standard /V 1 /R 2 /O <" + b"11" * 32 + b"> /U <" + b"22" * 32
           + b"> /P -4 >>")
rc, out, err = run(pdf(text, encrypt=rc4_bad))
check("user password required refused", (rc, out, "password" in err), (3, "", True))

check("empty file gives no text", run(b"")[:2], (0, ""))

print("\nMind atment." if not FAILED else f"\n{len(FAILED)} hiba.")
sys.exit(1 if FAILED else 0)
