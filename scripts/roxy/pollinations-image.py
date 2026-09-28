#!/usr/bin/env python3
"""
Generate an image with Pollinations. No API key, no account, no bill.

Why this exists
---------------
Google's image models refuse on a free key: the free-tier quota for image
generation is not small, it is 0 (measured 2026-08-30 on six models, including
the lite ones). Every other candidate -- Leonardo, Ideogram, Recraft, fal.ai --
wants a paid API plan, and even the "free tier" ones (Hugging Face, Cloudflare)
want an account and a token first. This one needs nothing, so it answers the only
question worth asking before anyone pays: is a free model good enough for THIS
work?

The seed is the point
---------------------
Passing the same --seed with the same character description is how the same
character comes back on the next picture, which is what a 40-picture book needs.
It is weaker than what a paid model does with a reference image, and that gap is
exactly what the comparison is meant to show.

Usage
-----
  pollinations-image.py --prompt "..." --out FILE.jpg [--seed N] [--size 1024]
"""
import argparse
import os
import sys
import urllib.parse
import urllib.request

BASE = "https://image.pollinations.ai/prompt/"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--model", default=None)
    a = ap.parse_args(argv)

    params = {"width": a.width, "height": a.height, "nologo": "true"}
    if a.seed is not None:
        params["seed"] = a.seed
    if a.model:
        params["model"] = a.model
    url = BASE + urllib.parse.quote(a.prompt) + "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers={"User-Agent": "marveen-roxy/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read()
    except Exception as e:
        sys.stderr.write(f"HIBA: {type(e).__name__}: {e}\n")
        return 2

    # Check the magic bytes rather than the status code. A generator that is
    # overloaded can answer 200 with an HTML error page, and a .jpg full of HTML
    # looks like a success until someone opens it.
    if data[:2] == b"\xff\xd8":
        kind = "JPEG"
    elif data[:4] == b"\x89PNG":
        kind = "PNG"
    else:
        sys.stderr.write(f"NEM KEP jott vissza ({len(data)} bajt). Eleje: {data[:80]!r}\n")
        return 2

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "wb") as fh:
        fh.write(data)
    print(f"OK {a.out} | {len(data)} bajt | {kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
