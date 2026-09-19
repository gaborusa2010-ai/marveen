#!/usr/bin/env python3
"""
Download ONE image from an approved host, without anybody looking at it.

Why this exists
---------------
The owner asked on 2026-08-28 whether we can read dimensions off plan images.
We can -- the Lyford Road cut list came off photographed drawings -- but the web
path never delivers a picture: the quarantine-reader uses WebFetch, which
returns text, so an `img` on a plan page reaches us as a link and nothing else.

His suggestion was a filter that checks whether an image is safe. That cannot
work, and the reason is worth writing down because it will be proposed again:
to judge an image you have to look at it, and looking is the exposure. There is
no scan that reports "this drawing contains an instruction" without reading the
drawing.

So this pipeline does not filter the input. It narrows the OUTPUT, which is the
same trick that already protects us from page text:

  1. this script   fetches the bytes and NEVER shows them to a model
  2. an isolated reader  opens the image but may only fill a fixed form
  3. imgform.py    checks the form's SHAPE by machine, not by model

Text painted into a picture then has nowhere to go: there is no free-text field
to carry it and the reader has no tool to act on it.

What this script does NOT protect against
-----------------------------------------
The numbers can still be wrong. A misread dimension arrives through a perfectly
safe channel and looks exactly like a correct one -- see the `region` field in
imgform.py, and the workshop agent's rule that a `drawing` result is a proposal,
never a final cut list.

It also cannot stop its own caller from opening the file it just wrote. The
separation is procedural on that side: only the isolated reader looks. What the
script does guarantee is that the bytes came from a host the owner approved.

The gate is asked, not reimplemented
------------------------------------
Host approval is decided by running scripts/hooks/egress-gate.mjs itself, with a
synthesised quarantine-tier payload. A second copy of the matching rules here
would be one edit away from disagreeing with the real gate, and a permission
check that disagrees with the permission check is worse than none: both look
right in review. One source of truth, at the price of a node process per fetch.

Redirects are NOT followed. A 3xx is reported with its Location so the caller
can run the whole check again on the new URL. Following them silently would let
an approved host hand us off to any host at all, which is exactly the hole the
allowlist exists to close.

Usage
-----
  fetch-image.py URL --out DIR [--cap 12] [--max-bytes N]

Prints a one-line JSON receipt on success (path, bytes, sha256, type). Exit 0
downloaded, 2 refused by the gate, 3 over the rolling 24h budget, 4 fetch failed,
5 not an image we accept.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO_ROOT, "scripts", "hooks", "egress-gate.mjs")
BUDGET = os.path.join(REPO_ROOT, "scripts", "fetch-budget.py")
LOG = os.path.join(REPO_ROOT, "store", "egress-blocked.log")

# The tier the gate opens only for the quarantine reader. This script belongs to
# that tier by design: it is the fetching half of it.
QUARANTINE_AGENT_TYPE = "quarantine-reader"

# 8 MB. A plan page's cut-list image is tens to hundreds of KB; a photographed
# drawing sheet a few MB. Well above that is not a plan, and the point of a cap
# is that an unexpected size is refused rather than discovered afterwards.
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

# Raster formats only, identified by their own bytes and not by the URL or the
# Content-Type, both of which the server chooses.
#
# SVG IS DELIBERATELY ABSENT. It is XML, it can carry script and arbitrary text,
# and "it is an image" is exactly the sentence under which it would slip past a
# reviewer. A vector plan is text -- if we ever want one, it goes down the text
# path where it is already handled, not down the image path.
MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]


def sniff(head):
    """Format name from the leading bytes, or None if we do not accept it."""
    for magic, name in MAGIC:
        if head.startswith(magic):
            return name
    # WEBP is RIFF....WEBP: the size field sits between the two markers.
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


class NoRedirects(urllib.request.HTTPRedirectHandler):
    """Turn any redirect into an error instead of quietly following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect to {newurl}", headers, fp)


def gate_allows(url):
    """Ask the real egress gate. Returns (allowed, reason)."""
    payload = json.dumps({
        "tool_name": "WebFetch",
        "agent_type": QUARANTINE_AGENT_TYPE,
        "tool_input": {"url": url},
    })
    try:
        # SELFTEST keeps the gate's own log line out of the budget: this call is
        # a permission question, not traffic. The download below writes the line
        # that does count.
        env = dict(os.environ, EGRESS_GATE_SELFTEST="1")
        p = subprocess.run(["node", GATE], input=payload, capture_output=True,
                           text=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        # FAIL-CLOSED. If the gate cannot be consulted we do not fetch. An
        # unavailable permission check is a "no", never a "probably fine".
        return False, f"a kapu nem futott: {e}"
    if p.stdout.strip():
        try:
            reason = json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        except (ValueError, KeyError):
            reason = p.stdout.strip()[:200]
        return False, reason
    # A CRASHED GATE IS NOT AN APPROVAL. Measured 2026-09-19: an allowed URL and a
    # gate that died on an unresolved import look identical on stdout -- both empty --
    # and only the exit code tells them apart. Without this branch a gate broken for
    # ANY reason (missing module, bad node, unreadable file) silently stops gating and
    # the fetch goes out. Seen for real in a clean git checkout, where the gate's
    # `lib/url-prefix.mjs` is absent: the block vanished and the download proceeded.
    # The spawn-failure branch above already fails closed; this is the same rule for a
    # failure that happens after the process starts.
    if p.returncode != 0:
        detail = (p.stderr or "").strip().splitlines()
        detail = detail[-1][:200] if detail else f"exit {p.returncode}"
        return False, f"a kapu hibaval allt le, tehat NEM engedely: {detail}"
    return True, ""


def budget_left(url, cap):
    """Ask fetch-budget.py. Returns (has_allowance, message)."""
    try:
        p = subprocess.run([sys.executable, BUDGET, "--budget", url, "--cap", str(cap)],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"a budget-check nem futott: {e}"
    return p.returncode == 0, p.stdout.strip()


def log_fetch(url, kind="ALLOWED_IMAGE"):
    """Write the line fetch-budget.py counts.

    Without this an image download is invisible to the per-source rolling 24h count,
    and the source would be paying for requests nothing measures -- which is the
    same blind spot that got us blocked from kregtool in the first place.
    """
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f'{ts} {kind} url="{url}" reason="gated image fetch" '
                     f'agent_type="{QUARANTINE_AGENT_TYPE}"\n')
    except OSError:
        pass  # never let logging failure stop or crash the caller


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", required=True, help="konyvtar, ahova a kep kerul")
    ap.add_argument("--cap", type=int, default=12,
                    help="plafon egy hostra, GORDULO 24 ORAS ablakra")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument("--skip-budget", action="store_true",
                    help="csak teszthez: a 24 oras plafon ellenorzesenek kihagyasa")
    a = ap.parse_args(argv)

    allowed, reason = gate_allows(a.url)
    if not allowed:
        print(f"ELUTASITVA a kapun: {reason}", file=sys.stderr)
        return 2

    if not a.skip_budget:
        ok, msg = budget_left(a.url, a.cap)
        print(msg, file=sys.stderr)
        if not ok:
            return 3

    req = urllib.request.Request(a.url, headers={
        # Say who we are. A source that decides to refuse us should be able to,
        # and pretending to be a browser to get past that is the behaviour that
        # cost us kregtool.
        "User-Agent": "Marveen-workshop/1.0 (plan image fetch; contact via site owner)",
        "Accept": "image/png,image/jpeg,image/webp,image/gif",
    })
    opener = urllib.request.build_opener(NoRedirects)
    try:
        with opener.open(req, timeout=30) as resp:
            # +1 so an exactly-at-the-limit file is still recognised as too big
            # rather than silently truncated into a corrupt image.
            body = resp.read(a.max_bytes + 1)
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location") if e.headers else None
        log_fetch(a.url)  # the request left the machine even though it failed
        if loc:
            print(f"ATIRANYITAS ({e.code}) -> {loc}\n"
                  f"Nem kovetem. Futtasd ujra EZZEL az url-lel, es a kapu ujra dont rola.",
                  file=sys.stderr)
        else:
            print(f"HTTP {e.code}: {e.reason}", file=sys.stderr)
        return 4
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"A letoltes nem sikerult: {e}", file=sys.stderr)
        return 4

    log_fetch(a.url)

    if len(body) > a.max_bytes:
        print(f"TUL NAGY: tobb mint {a.max_bytes} bajt. Nem mentem el.", file=sys.stderr)
        return 5

    kind = sniff(body[:16])
    if kind is None:
        # Say what it looked like, but never print the body: an error message is
        # a place where page content could reach a model through the back door.
        print(f"NEM ELFOGADOTT KEP. A szerver ezt mondta: {ctype!r}, "
              f"a fajl elso bajtjai viszont nem png/jpeg/gif/webp. "
              f"SVG-t szandekosan nem fogadunk el, az szoveg.", file=sys.stderr)
        return 5

    digest = hashlib.sha256(body).hexdigest()
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"{digest[:16]}.{kind}")
    with open(path, "wb") as fh:
        fh.write(body)

    print(json.dumps({
        "path": path,
        "bytes": len(body),
        "type": kind,
        "sha256": digest,
        "url": a.url,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
