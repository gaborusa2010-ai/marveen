#!/usr/bin/env python3
"""
Generate an image with fal.ai. urllib only, no pip dependencies.

fal.ai hosts many models behind one key (FLUX, SDXL, Ideogram and others), which
is why it is worth one test rather than one per vendor: change --model and the
same key reaches a different model.

Key: store/.fal-key, one line. Get one at fal.ai -> Dashboard -> Keys.

Usage
-----
  fal-image.py --prompt "..." --out FILE.jpg [--model fal-ai/flux/dev] [--ref URL_OR_FILE]

Exit codes: 0 written, 2 the API refused, 3 no key.
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request

KEY_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "store", ".fal-key")
DEFAULT_MODEL = "fal-ai/flux/dev"


def read_key():
    try:
        with open(KEY_FILE, encoding="utf-8") as fh:
            key = fh.read().strip()
    except OSError:
        key = ""
    if not key:
        sys.stderr.write(f"Nincs fal.ai kulcs. Varom itt: {os.path.abspath(KEY_FILE)}\n")
        sys.exit(3)
    return key


def post(url, payload, key):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Key {key}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        sys.stderr.write(f"fal.ai elutasitotta ({e.code}): {body[:500]}\n")
        sys.exit(2)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ref", help="referenciakep: URL vagy helyi fajl")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    a = ap.parse_args(argv)

    payload = {"prompt": a.prompt, "image_size": {"width": a.width, "height": a.height}}
    if a.ref:
        if os.path.exists(a.ref):
            mime = mimetypes.guess_type(a.ref)[0] or "image/png"
            with open(a.ref, "rb") as fh:
                payload["image_url"] = f"data:{mime};base64," + base64.b64encode(fh.read()).decode()
        else:
            payload["image_url"] = a.ref

    data = post(f"https://fal.run/{a.model}", payload, read_key())

    imgs = data.get("images") or []
    if not imgs:
        sys.stderr.write("A valaszban nincs kep: " + json.dumps(data)[:500] + "\n")
        return 2
    url = imgs[0].get("url")
    if not url:
        sys.stderr.write("A kepnek nincs url-je: " + json.dumps(imgs[0])[:300] + "\n")
        return 2

    # fal answers with a URL, not with the bytes, so the picture still has to be
    # fetched. A data: URL is possible too, and urlopen handles both.
    # The CDN serving the finished picture rejects a request with no
    # User-Agent (403), even though the generation itself succeeded. Without
    # this the run looks like a permission problem when the image is in fact
    # ready and already paid for.
    imreq = urllib.request.Request(url, headers={"User-Agent": "marveen-roxy/1.0"})
    with urllib.request.urlopen(imreq, timeout=300) as r:
        blob = r.read()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "wb") as fh:
        fh.write(blob)
    print(f"OK {a.out} | {len(blob)} bajt | modell: {a.model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
