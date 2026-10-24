#!/usr/bin/env python3
"""--pdf (#629): is a factory PDF kept, and is everything else thrown away?

Runs with ZERO external network traffic: a local HTTP server on 127.0.0.1 plays
the manufacturer, and the allowlist, budget, egress log and quarantine folder
are swapped for temp files, so no real host's budget is spent and no real log
gets a line.
"""
import contextlib
import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "browser-fetch"))
import browser_fetch  # noqa: E402

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
BIG = PDF + b"0" * (1024 * 1024 + 10)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype, disp=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if disp:
            self.send_header("Content-Disposition", disp)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.end_headers()

    def do_GET(self):
        port = self.server.server_address[1]
        routes = {
            "/att.pdf": lambda: self.send(PDF, "application/pdf", 'attachment; filename="x.pdf"'),
            "/inline.pdf": lambda: self.send(PDF, "application/pdf"),
            "/page": lambda: self.send(b"<html><title>t</title><body>hi</body></html>", "text/html"),
            "/fake.pdf": lambda: self.send(b"<html>not a pdf</html>", "application/octet-stream",
                                           'attachment; filename="f.pdf"'),
            "/big.pdf": lambda: self.send(BIG, "application/pdf", 'attachment; filename="b.pdf"'),
            "/redir": lambda: self.redirect("/att.pdf"),
            "/away": lambda: self.redirect(f"http://localhost:{port}/att.pdf"),
        }
        routes.get(self.path, lambda: (self.send_response(404), self.end_headers()))()


fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
RENDER = os.path.join(ROOT, "scripts", "browser-fetch", "render.mjs")


def render(path, out, max_bytes):
    r = subprocess.run(["node", RENDER, BASE + path, "--download", out, str(max_bytes)],
                       capture_output=True, text=True, timeout=180)
    return r.returncode, (json.loads(r.stdout) if r.returncode == 0 else r.stderr)


with tempfile.TemporaryDirectory() as d:
    # --- 1. the renderer half ---------------------------------------------------
    rc, probe = render("/att.pdf", os.path.join(d, "probe.pdf"), 10000)
    if rc != 0:
        print("KIHAGYVA: a renderelo nem futott (playwright hianyzik?): " + str(probe)[:200])
    else:
        check("csatolmany-PDF: elmentve, %PDF- fejjel",
              probe["download"]["saved"] and probe["download"]["head"].startswith("%PDF-"), probe)
        _, j = render("/inline.pdf", os.path.join(d, "inline.pdf"), 10000)
        check("inline application/pdf valasz is elmentodik", (j.get("download") or {}).get("saved"), j)
        _, j = render("/page", os.path.join(d, "page.pdf"), 10000)
        check("sima lapnal nincs letoltes", j.get("download") is None
              and not os.path.exists(os.path.join(d, "page.pdf")), j)
        _, j = render("/big.pdf", os.path.join(d, "big.pdf"), 1000)
        check("plafon feletti fajl NINCS kiirva", not j["download"]["saved"]
              and not os.path.exists(os.path.join(d, "big.pdf")), j)
        _, j = render("/redir", os.path.join(d, "redir.pdf"), 10000)
        check("atiranyitasnal a letoltes URL-je a VEGSO cim",
              (j.get("download") or {}).get("url") == BASE + "/att.pdf", j)
        rc2, err = render("/att.pdf", os.path.join(d, "x.pdf"), 51 * 1024 * 1024)
        check("50 MB feletti plafont a renderelo maga is elutasit", rc2 == 2, err)

    # --- 2. pdf_problem, no browser ---------------------------------------------
    allow = os.path.join(d, "allow.json")
    with open(allow, "w", encoding="utf-8") as fh:
        json.dump({"quarantine_domains": ["127.0.0.1"]}, fh)
    browser_fetch.ALLOWLIST = allow
    good = os.path.join(d, "good.pdf")
    with open(good, "wb") as fh:
        fh.write(PDF)
    fake = os.path.join(d, "fake.pdf")
    with open(fake, "wb") as fh:
        fh.write(b"<html>no")
    ok_dl = {"url": BASE + "/att.pdf", "bytes": len(PDF), "saved": True, "head": "%PDF-1.4"}
    def P(*args):
        return browser_fetch.file_problem(*args)[0]
    check("jo PDF-re nincs kifogas", P({"download": ok_dl}, good, 10000) is None)
    check("renderelo-hibara kifogas", P({"error": "x", "download": ok_dl}, good, 10000) is not None)
    check("letoltes nelkul kifogas", P({"download": None, "status": 200}, good, 10000) is not None)
    check("idegen vegso hoszt kifogas",
          P({"download": dict(ok_dl, url="http://evil.example/x.pdf")}, good, 10000) is not None)
    check("plafon felett kifogas", P({"download": dict(ok_dl, bytes=20000)}, good, 10000) is not None)
    check("nem mentett fajlra kifogas", P({"download": dict(ok_dl, saved=False)}, good, 10000) is not None)
    check("%PDF- nelkuli fajlra kifogas", P({"download": ok_dl}, fake, 10000) is not None)

    # --- 3. main --pdf end to end ------------------------------------------------
    if rc == 0:
        budget = os.path.join(d, "budget.py")
        with open(budget, "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.exit(0)\n")
        browser_fetch.BUDGET = budget
        browser_fetch.EGRESS_LOG = os.path.join(d, "egress.log")
        browser_fetch.OUTDIR = os.path.join(d, "q")
        card = os.path.join(d, "q", "a898aaaf")

        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            sys.argv = ["browser_fetch.py", *argv]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = browser_fetch.main()
            return code, out.getvalue(), err.getvalue()

        def pdfs():
            return sorted(f for f in os.listdir(card) if f.endswith(".pdf")) if os.path.isdir(card) else []

        def leftovers():
            names = os.listdir(card) if os.path.isdir(card) else []
            return [f for f in names if f.endswith(".part")], len([f for f in names if f.endswith(".meta.txt")])

        code, out, err = run(BASE + "/att.pdf", "--pdf", "a898aaaf")
        check("main: jo PDF -> exit 0, egy PDF a kartya mappajaban", code == 0 and len(pdfs()) == 1,
              (code, out, err, pdfs()))
        meta = [f for f in os.listdir(card) if f.endswith(".meta.txt")] if os.path.isdir(card) else []
        body = open(os.path.join(card, meta[0]), encoding="utf-8").read() if meta else ""
        check("main: a meta-fajlban sha256 es a szerver fajlnev-javaslata", "SHA256:" in body
              and "x.pdf" in body, body)
        check("main: a szerver fajlneve NEM kerul stdoutra", '"x.pdf"' not in out
              and "/x.pdf" not in out, out)
        check("main: az egress-naploba egy ALLOWED_BROWSER sor", open(browser_fetch.EGRESS_LOG)
              .read().count("ALLOWED_BROWSER") == 1)

        for path, extra, why in (("/fake.pdf", [], "nem PDF"),
                                 ("/big.pdf", ["--max-mb", "1"], "plafon felett"),
                                 ("/page", [], "lap, nem letoltes"),
                                 ("/away", [], "atiranyitas idegen hosztra")):
            code, out, err = run(BASE + path, "--pdf", "a898aaaf", *extra)
            check(f"main: {why} -> exit 6, nem marad fajl", code == 6 and len(pdfs()) == 1,
                  (code, err, pdfs()))
        part, metas = leftovers()
        check("main: bukas utan nem marad .part, es meta csak a megtartott PDF-hez", not part and metas == 1,
              (part, metas))
        check("main: a bukott lekeresek is szamolva vannak", open(browser_fetch.EGRESS_LOG)
              .read().count("ALLOWED_BROWSER") == 5)

        code, _, err = run(BASE + "/att.pdf", "--pdf", "../x")
        check("main: perjeles kartya-azonosito -> exit 2", code == 2, err)
        code, _, err = run(BASE + "/att.pdf", "--pdf", "a898aaaf", "--links")
        check("main: --pdf mas moddal -> exit 2", code == 2, err)
        code, _, err = run(BASE + "/att.pdf", "--pdf", "a898aaaf", "--max-mb", "51")
        check("main: --max-mb 51 -> exit 2", code == 2, err)
        code, _, err = run("http://evil.example/x.pdf", "--pdf", "a898aaaf")
        check("main: nem allowlistes hoszt -> exit 3", code == 3, err)

srv.shutdown()
print()
print(f"{len(fails)} FAIL" if fails else "MIND ATMENT")
sys.exit(1 if fails else 0)
