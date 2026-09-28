#!/usr/bin/env python3
"""
Generate an image with Google's Gemini image model ("Nano Banana"). No pip
dependencies -- urllib only, like the rest of the fleet's helpers.

Why this is a script and not a WebFetch
--------------------------------------
The egress gate (scripts/hooks/egress-gate.mjs) is registered on the WebFetch
matcher only, so a urllib call from here is not gated and needs no allowlist
entry. That is deliberate: widening store/egress-allowlist.json is a permission
change, and this call does not require one.

Usage
-----
  gemini-image.py --prompt "..." --out FILE.png [--model M] [--ref IMAGE]

--ref passes an existing image back in, which is how the same character is kept
across pictures: generate once, then feed that picture in with the next prompt.

Exit codes: 0 written, 2 the API refused (message printed), 3 no key.
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request

KEY_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "store", ".gemini-key")
DEFAULT_MODEL = "gemini-2.5-flash-image"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def read_key():
    try:
        with open(KEY_FILE, encoding="utf-8") as fh:
            key = fh.read().strip()
    except OSError:
        key = ""
    if not key:
        sys.stderr.write(
            "Nincs Gemini kulcs.\n"
            f"  Varom itt: {os.path.abspath(KEY_FILE)}\n"
            "  Egy sor, csak a kulcs. Letrehozas: aistudio.google.com -> API key\n"
        )
        sys.exit(3)
    return key


def call(model, prompt, ref_path, key):
    parts = [{"text": prompt}]
    if ref_path:
        mime = mimetypes.guess_type(ref_path)[0] or "image/png"
        with open(ref_path, "rb") as fh:
            parts.insert(0, {"inline_data": {"mime_type": mime,
                                             "data": base64.b64encode(fh.read()).decode()}})
    body = json.dumps({"contents": [{"parts": parts}]}).encode()
    req = urllib.request.Request(
        ENDPOINT.format(model=model),
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        # The interesting part of a Google error is the message, not the wall of
        # JSON around it -- print both, short first, so a billing refusal reads
        # as a sentence rather than as a stack trace.
        try:
            msg = json.loads(detail)["error"]["message"]
        except Exception:
            msg = detail[:400]
        sys.stderr.write(f"A Google elutasitotta ({e.code}): {msg}\n")
        sys.stderr.write(f"  teljes valasz: {detail[:800]}\n")
        sys.exit(2)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ref", help="korabbi kep, a karakter-konzisztenciahoz")
    a = ap.parse_args(argv)

    data = call(a.model, a.prompt, a.ref, read_key())

    # Walk every candidate part: the model answers with text AND image parts, and
    # which one comes first is not guaranteed. Taking parts[0] blindly is how a
    # run ends up writing a text blob into a .png.
    text_bits = []
    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                with open(a.out, "wb") as fh:
                    fh.write(base64.b64decode(blob["data"]))
                print(f"OK {a.out} ({os.path.getsize(a.out)} bajt)")
                if text_bits:
                    print("  a modell szoveget is kuldott: " + " ".join(text_bits)[:200])
                return 0
            if part.get("text"):
                text_bits.append(part["text"].strip())

    sys.stderr.write("A valaszban NINCS kep.\n")
    if text_bits:
        sys.stderr.write("  a modell ezt mondta: " + " ".join(text_bits)[:600] + "\n")
    else:
        sys.stderr.write("  nyers valasz: " + json.dumps(data)[:600] + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
