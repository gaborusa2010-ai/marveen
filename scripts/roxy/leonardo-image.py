#!/usr/bin/env python3
"""
Generate an image with Leonardo.ai. urllib only, no pip dependencies.

Leonardo is two-step, unlike the others: the POST only QUEUES the job and hands
back an id, and the picture has to be polled for. A caller written as if it were
one-shot gets an empty answer and reads it as failure.

Key: store/.leonardo-key, one line. Get one at app.leonardo.ai -> API Access.
NOTE: API access is a separate paid plan from the web subscription; a web-only
account's key will be refused, and that refusal is the answer, not a bug.

Usage
-----
  leonardo-image.py --prompt "..." --out FILE.jpg [--model UUID] [--width N] [--height N]

Exit codes: 0 written, 2 the API refused or gave nothing, 3 no key, 4 timed out.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

KEY_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "store", ".leonardo-key")
API = "https://cloud.leonardo.ai/api/rest/v1"
# Leonardo Phoenix -- their general-purpose model. Overridable with --model.
DEFAULT_MODEL = "6b645e3a-d64f-4341-a6d8-7a3690fbf042"


def read_key():
    try:
        with open(KEY_FILE, encoding="utf-8") as fh:
            key = fh.read().strip()
    except OSError:
        key = ""
    if not key:
        sys.stderr.write(f"Nincs Leonardo kulcs. Varom itt: {os.path.abspath(KEY_FILE)}\n")
        sys.exit(3)
    return key


def req(url, key, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        sys.stderr.write(f"Leonardo elutasitotta ({e.code}): {body[:500]}\n")
        sys.exit(2)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--wait", type=int, default=240, help="meddig varjunk a kepre, mp")
    a = ap.parse_args(argv)

    key = read_key()
    started = req(f"{API}/generations", key, {
        "prompt": a.prompt, "modelId": a.model,
        "width": a.width, "height": a.height, "num_images": 1})
    gid = ((started.get("sdGenerationJob") or {}).get("generationId"))
    if not gid:
        sys.stderr.write("Nem kaptunk generationId-t: " + json.dumps(started)[:400] + "\n")
        return 2
    print(f"  varakozas a keprre (id {gid}) ...", file=sys.stderr)

    deadline = time.time() + a.wait
    url = None
    while time.time() < deadline:
        time.sleep(5)
        got = req(f"{API}/generations/{gid}", key)
        job = got.get("generations_by_pk") or {}
        if job.get("status") == "FAILED":
            sys.stderr.write("A generalas FAILED statusszal allt le.\n")
            return 2
        imgs = job.get("generated_images") or []
        if imgs and imgs[0].get("url"):
            url = imgs[0]["url"]
            break
    if not url:
        sys.stderr.write(f"{a.wait} mp alatt nem lett kesz a kep.\n")
        return 4

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
    print(f"OK {a.out} | {len(blob)} bajt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
