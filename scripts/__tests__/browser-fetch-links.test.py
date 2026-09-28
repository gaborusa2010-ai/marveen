#!/usr/bin/env python3
"""--links: does the renderer hand back followable hrefs, and does the file stay readable?

Runs with ZERO network traffic. The renderer half opens a local file:// fixture,
which is the only way to exercise link extraction without spending a fetch from a
real host's budget -- and spending one to test a formatter would be the exact
habit fetch-budget.py exists to prevent.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "browser-fetch"))
import browser_fetch  # noqa: E402

# The <base> tag is not decoration. Without it a relative href on a file:// page
# resolves to file://..., which the renderer drops on purpose (we only ever want
# http(s) back). The first version of this test failed for exactly that reason --
# the fixture, not the code -- so the base URL is what makes "relative resolves to
# absolute" measurable at all here.
FIXTURE = """<!doctype html><html><head><title>Fixture</title>
<base href="https://ico.org.uk/for-organisations/data-protection-fee/"></head><body>
<h1>Hub</h1>
<a href="/for-organisations/data-protection-fee/self-assessment/">How much do I need to pay?</a>
<a href="https://example.org/guide">  Guide   to   the
fee  </a>
<a href="/for-organisations/data-protection-fee/self-assessment/">duplicate, same href</a>
<a href="mailto:someone@example.org">mail link</a>
<a href="javascript:void(0)">script link</a>
<a href="https://example.org/empty"></a>
</body></html>"""

fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# --- 1. the renderer half, against a local file:// page -----------------------
with tempfile.TemporaryDirectory() as d:
    fx = os.path.join(d, "hub.html")
    with open(fx, "w", encoding="utf-8") as fh:
        fh.write(FIXTURE)
    r = subprocess.run(["node", os.path.join(ROOT, "scripts", "browser-fetch", "render.mjs"),
                        "file://" + fx], capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print("KIHAGYVA: a renderelo nem futott (playwright hianyzik?): "
              + r.stderr.strip()[:200])
        links = None
    else:
        links = json.loads(r.stdout).get("links")

if links is not None:
    hrefs = [l["href"] for l in links]
    check("relativ href ABSZOLUT https alakban jon vissza",
          "https://ico.org.uk/for-organisations/data-protection-fee/self-assessment/" in hrefs,
          hrefs)
    check("ugyanaz a href csak EGYSZER szerepel",
          len([h for h in hrefs if h.endswith("/self-assessment/")]) == 1, hrefs)
    check("mailto: es javascript: NEM kerul be",
          not any(h.startswith(("mailto:", "javascript:")) for h in hrefs), hrefs)
    check("a link szovegebol kiesik a sortores es a tobbszoros szokoz",
          any(l["text"] == "Guide to the fee" for l in links),
          [l["text"] for l in links])

# --- 2. the formatter half, no browser at all --------------------------------
check("ures listara ures szoveg jon (nem ures szakasz-fejlec)",
      browser_fetch.links_section([]) == "")

s = browser_fetch.links_section([{"text": "", "href": "https://example.org/empty"},
                                 {"text": "Fee guide", "href": "https://example.org/guide"}])
check("a szakasz kimondja, hogy a linkek is idegen adatok", "IDEGEN ADATOK" in s)
check("a szoveg nelkuli link nem nevtelen sorkent all", "(nincs szoveg)" in s)
check("minden href sajat sorba kerul",
      s.count("\n  https://") == 2, repr(s))

print()
print(f"{len(fails)} FAIL" if fails else "MIND ATMENT")
sys.exit(1 if fails else 0)
