#!/usr/bin/env python3
"""--file (owner, Telegram 656): CAD and Excel/Word kept by their own bytes, macros refused.

Runs with ZERO external network traffic, like browser-fetch-pdf.test.py: a local
server on 127.0.0.1 plays the manufacturer, and the allowlist, budget, egress log
and quarantine folder are temp files.
"""
import contextlib
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "browser-fetch"))
import browser_fetch  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def office_zip(*names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        for n in names:
            z.writestr(*(n if isinstance(n, tuple) else (n, "<x/>")))
    return buf.getvalue()


REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def rels(body):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + body + "</Relationships>")


def ole(names, extra=b""):
    """A minimal OLE2 file: header sector, one directory sector, then `extra`."""
    def entry(n):
        raw = n.encode("utf-16-le")
        return raw + b"\x00" * (64 - len(raw)) + (len(raw) + 2).to_bytes(2, "little") + b"\x00" * 62
    d = b"".join(entry(n) for n in names)
    return (browser_fetch.OLE_MAGIC + b"\x00" * 504 + d + b"\x00" * (-len(d) % 512)) + extra


FIX = {
    "dxf_text": b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n  0\r\nENDSEC\r\n  0\r\nEOF\r\n",
    "dxf_comment": b"999\ndxfrw 0.6.3\n  0\nSECTION\n  2\nHEADER\n",
    "dxf_binary": b"AutoCAD Binary DXF\r\n\x1a\x00" + b"\x00" * 40,
    "dwg": b"AC1032" + b"\x00" * 100,
    "step": b"\xef\xbb\xbfISO-10303-21;\nHEADER;\nENDSEC;\n",
    "xlsx": office_zip("xl/workbook.xml"),
    "xlsm": office_zip("xl/workbook.xml", "xl/vbaProject.bin"),
    "docx": office_zip("word/document.xml"),
    "docm": office_zip("word/document.xml", "word/vbaProject.bin"),
    "xlsx_macrosheet": office_zip("xl/workbook.xml", "xl/macrosheets/sheet1.xml"),
    "xlsx_activex": office_zip("xl/workbook.xml", "xl/activeX/activeX1.xml"),
    "docx_remote_tpl": office_zip("word/document.xml", ("word/_rels/settings.xml.rels",
        rels('<Relationship Id="r1" Type="' + REL + 'attachedTemplate" Target="http://x.example/t.dotm" TargetMode="External"/>'))),
    # Same, written the other legal ways: apostrophes, spaces around '='.
    "docx_remote_tpl_quotes": office_zip("word/document.xml", ("word/_rels/settings.xml.rels",
        rels("<Relationship Id='r1' Type = '" + REL + "attachedTemplate' Target='http://x.example/t.dotm' TargetMode = 'External'/>"))),
    # Follina (CVE-2022-30190): an external oleObject in document.xml.rels.
    "docx_follina": office_zip("word/document.xml", ("word/_rels/document.xml.rels",
        rels('<Relationship Id="r9" Type="' + REL + 'oleObject" Target="mhtml:http://x.example/!x-usc:http://x.example/" TargetMode="External"/>'))),
    "xlsx_external_link": office_zip("xl/workbook.xml", ("xl/externalLinks/_rels/externalLink1.xml.rels",
        rels('<Relationship Id="r1" Type="' + REL + 'externalLinkPath" Target="http://x.example/b.xlsx" TargetMode="External"/>'))),
    "docx_local_tpl": office_zip("word/document.xml", ("word/_rels/settings.xml.rels",
        rels('<Relationship Id="r1" Type="' + REL + 'attachedTemplate" Target="Normal.dotm"/>'))),
    # Negative controls: an external hyperlink is just a link, in any .rels part.
    "docx_ext_other": office_zip("word/document.xml", ("word/_rels/settings.xml.rels",
        rels('<Relationship Id="r1" Type="' + REL + 'hyperlink" Target="http://x.example/" TargetMode="External"/>'))),
    "docx_ext_hyperlink": office_zip("word/document.xml", ("word/_rels/document.xml.rels",
        rels("<Relationship Id='r2' Type='" + REL + "hyperlink' Target='http://x.example/' TargetMode='External'/>"))),
    "docx_rels_dtd": office_zip("word/document.xml", ("word/_rels/document.xml.rels",
        rels("").replace("?><", '?><!DOCTYPE Relationships [<!ENTITY a "aaaa">]><', 1))),
    "docx_rels_broken": office_zip("word/document.xml", ("word/_rels/document.xml.rels", "<Relationships")),
    "zip_other": office_zip("readme.txt"),
    "zip_broken": b"PK\x03\x04" + b"\x00" * 30,
    "xls": ole(["Root Entry", "Workbook"]),
    "xls_macro": ole(["Root Entry", "Workbook", "_VBA_PROJECT_CUR", "VBA", "_VBA_PROJECT"]),
    "doc": ole(["Root Entry", "WordDocument", "1Table"]),
    "doc_macro": ole(["Root Entry", "WordDocument", "Macros", "VBA", "_VBA_PROJECT"]),
    # An OLE file that only MENTIONS "Workbook" in its data, off any entry boundary.
    "ole_mention": ole(["Root Entry", "Data"], b"\x00\x00\x00" + "Workbook".encode("utf-16-le") + b"\x00" * 64),
    "html": b"<html><body>not a drawing</body></html>",
    "pdf": b"%PDF-1.4\n%%EOF\n",
}
EXPECT = {
    "dxf_text": ("dxf", None), "dxf_comment": ("dxf", None), "dxf_binary": ("dxf", None),
    "dwg": ("dwg", None), "step": ("step", None),
    "xlsx": ("xlsx", None), "xlsm": ("xlsx", "makrot tartalmaz"),
    "docx": ("docx", None), "docm": ("docx", "makrot tartalmaz"),
    "xlsx_macrosheet": ("xlsx", "makrot tartalmaz"), "xlsx_activex": ("xlsx", "makrot tartalmaz"),
    "docx_remote_tpl": ("docx", "kulso hivatkozast tartalmaz"),
    "docx_remote_tpl_quotes": ("docx", "kulso hivatkozast tartalmaz"),
    "docx_follina": ("docx", "kulso hivatkozast tartalmaz"),
    "xlsx_external_link": ("xlsx", "kulso hivatkozast tartalmaz"),
    "docx_local_tpl": ("docx", None), "docx_ext_other": ("docx", None), "docx_ext_hyperlink": ("docx", None),
    "docx_rels_dtd": (None, "olvashatatlan hivatkozas-tabla"),
    "docx_rels_broken": (None, "olvashatatlan hivatkozas-tabla"),
    "zip_other": (None, "ZIP, de nem Excel vagy Word"), "zip_broken": (None, "serult ZIP-tarolo"),
    "xls": ("xls", None), "xls_macro": ("xls", "makrot tartalmaz"),
    "doc": ("doc", None), "doc_macro": ("doc", "makrot tartalmaz"),
    "ole_mention": (None, "regi Office-tarolo, de nem Excel vagy Word"),
    "html": (None, None), "pdf": ("pdf", None),
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        name, _, mode = self.path.lstrip("/").partition(".")
        body = FIX.get(name)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        if mode == "inline":
            self.send_header("Content-Type", "text/plain")
        else:
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{name}.bin"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


with tempfile.TemporaryDirectory() as d:
    # --- 1. sniff, no browser -----------------------------------------------------
    for name, body in FIX.items():
        p = os.path.join(d, name)
        with open(p, "wb") as fh:
            fh.write(body)
        got = browser_fetch.sniff(p)
        check(f"sniff {name} -> {EXPECT[name]}", got == EXPECT[name], got)

    allow = os.path.join(d, "allow.json")
    with open(allow, "w", encoding="utf-8") as fh:
        json.dump({"quarantine_domains": ["127.0.0.1"]}, fh)
    browser_fetch.ALLOWLIST = allow
    dl = {"url": "http://127.0.0.1/x", "bytes": 10, "saved": True, "head": "AC1032.."}
    p_dwg = os.path.join(d, "dwg")
    problem, kind = browser_fetch.file_problem({"download": dl}, p_dwg, 10000, browser_fetch.FILE_KINDS)
    check("file_problem: DWG --file modban rendben", problem is None and kind == "dwg", (problem, kind))
    problem, _ = browser_fetch.file_problem({"download": dl}, p_dwg, 10000, ("pdf",))
    check("file_problem: DWG --pdf modban 'nem PDF'", (problem or "").startswith("nem PDF"), problem)
    problem, _ = browser_fetch.file_problem({"download": dl}, os.path.join(d, "xlsm"), 10000,
                                            browser_fetch.FILE_KINDS)
    check("file_problem: makros xlsx elutasitva, az ok kimondva", "makrot" in (problem or ""), problem)

    # --- 2. main --file end to end ------------------------------------------------
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    budget = os.path.join(d, "budget.py")
    with open(budget, "w", encoding="utf-8") as fh:
        fh.write("import sys\nsys.exit(0)\n")
    browser_fetch.BUDGET = budget
    browser_fetch.EGRESS_LOG = os.path.join(d, "egress.log")
    browser_fetch.OUTDIR = os.path.join(d, "q")
    card = os.path.join(d, "q", "cacefb93")

    def run(*argv):
        out, err = io.StringIO(), io.StringIO()
        sys.argv = ["browser_fetch.py", *argv]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = browser_fetch.main()
        return code, out.getvalue(), err.getvalue()

    def files():
        return sorted(os.listdir(card)) if os.path.isdir(card) else []

    code, out, err = run(base + "/dwg", "--file", "cacefb93")
    if code == 5 and "playwright" in err.lower():
        print("KIHAGYVA: a renderelo nem futott: " + err[:200])
    else:
        kept = [f for f in files() if f.endswith(".dwg")]
        check("main: DWG letoltes -> exit 0, .dwg kiterjesztes a bajtokbol", code == 0 and len(kept) == 1,
              (code, out, err, files()))
        meta = [f for f in files() if f.endswith(".meta.txt")]
        body = open(os.path.join(card, meta[0]), encoding="utf-8").read() if meta else ""
        check("main: a meta-fajlban a tipus es a szerver fajlnev-javaslata",
              "# TIPUS: dwg" in body and "dwg.bin" in body, body)
        check("main: a szerver fajlneve NEM kerul stdoutra", "dwg.bin" not in out, out)

        code, out, err = run(base + "/dxf_text.inline", "--file", "cacefb93")
        check("main: text/plain-kent kiszolgalt DXF is megmarad (inline ag)",
              code == 0 and any(f.endswith(".dxf") for f in files()), (code, err, files()))
        code, out, err = run(base + "/step", "--file", "cacefb93")
        check("main: STEP -> .step", code == 0 and any(f.endswith(".step") for f in files()), (code, err))
        modes = {f: os.stat(os.path.join(card, f)).st_mode & 0o111 for f in files()}
        check("main: a megtartott fajl es a meta nem futtathato", modes and not any(modes.values()), modes)
        code, out, err = run(base + "/xlsx", "--file", "cacefb93")
        check("main: XLSX -> .xlsx", code == 0 and any(f.endswith(".xlsx") for f in files()), (code, err))

        before = files()
        for name, why in (("xlsm", "makros Excel"), ("doc_macro", "makros regi Word"),
                          ("html.inline", "HTML lap"), ("zip_other", "nem Office ZIP")):
            code, out, err = run(base + "/" + name, "--file", "cacefb93")
            check(f"main: {why} -> exit 6, nem marad utana semmi", code == 6 and files() == before,
                  (code, err, sorted(set(files()) - set(before))))
        code, out, err = run(base + "/dwg", "--pdf", "cacefb93")
        check("main: --pdf modban a DWG nem marad meg", code == 6 and "nem PDF" in err
              and files() == before, (code, err))
        n = open(browser_fetch.EGRESS_LOG).read().count("ALLOWED_BROWSER")
        check("main: minden lekeres szamolva, a bukottak is (9 ALLOWED_BROWSER sor)", n == 9, n)
        code, _, err = run(base + "/dwg", "--file", "cacefb93", "--pdf", "x")
        check("main: --pdf es --file egyutt -> exit 2", code == 2, err)
    srv.shutdown()

print()
print(f"{len(fails)} FAIL" if fails else "MIND ATMENT")
sys.exit(1 if fails else 0)
