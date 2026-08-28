#!/usr/bin/env python3
"""
Check what came back from LOOKING at an image, as opposed to measuring one.

Why a second checker
--------------------
scripts/muhely/imgform.py handles the workshop case: a cut list or a dimensioned
drawing, out into numbers. The owner asked for the other half on 2026-08-28 --
"search Pinterest for pictures on a topic, look at them, decide which ones fit,
download those" -- and that needs the one thing the cut-list form deliberately
cannot express: what the picture IS.

A description is free text, and free text out of an untrusted image is exactly
the channel every other file here works to close. So there are two modes, and
they do not carry the same risk. Say which one you are in and mean it.

MATCH -- safe by construction
    The CALLER supplies the candidate labels. The viewer may only choose from
    that list; a label that was not offered is a rejected answer, not a new
    finding. Nothing the image contains can appear in the output, because the
    output is drawn from words the caller wrote. This is the mode for searching
    and sifting, which is most of the work: "which of these fifty pins show
    under-stair storage" is a closed question.

DESCRIBE -- as safe as reading a web page, and no safer
    A short caption in the viewer's own words. This IS free text. The length cap
    and the character rules below make a long, convincing injection awkward;
    they do not make one impossible, and pretending otherwise would be the same
    flattery this project already had to take out of imgform.py once.
    What actually holds the line is the same thing that holds it for page text
    we read every day: the caption comes back wrapped in an <untrusted> tag with
    a nonce, it is never presented as an instruction, and no tool takes it as an
    argument. Use this mode when a person is going to read the answer.

`kind` is required in both modes and is a closed set. It is the cheapest useful
fact about an image -- photo, drawing, render, screenshot -- and it costs the
viewer nothing to state.

Confidence is a word, not a number. A number invites a precision the viewer does
not have, and this repo spent a day on numbers that meant less than they looked.

Usage
-----
  imgview.py ANSWER.json --labels "a,b,c" [--nonce SENT] [--clean OUT.json]
  imgview.py ANSWER.json --describe [--nonce SENT] [--clean OUT.json]

Exit 0 well-shaped, 1 rejected.
"""
import argparse
import json
import re
import sys

DOC_KEYS = {"nonce", "mode", "kind", "matches", "caption", "readable"}
KINDS = ("photo", "drawing", "render", "diagram", "screenshot", "text_page", "other")
CONFIDENCE = ("high", "medium", "low")
MODES = ("match", "describe")

NONCE_MIN, NONCE_MAX = 8, 64
LABEL_MAX = 60
CAPTION_MAX = 200

# A caption needs ordinary sentence punctuation to be readable at all, so the
# charset here is wider than imgform's part names -- and therefore weaker. It
# rules out the shapes a caption never needs and an injection often does: markup
# brackets, code fences, newlines, and anything that looks like an address to
# send something to.
CAPTION_BANNED_RX = re.compile(r'[<>{}\[\]`|\\\n\r\t]|://|www\.|\b\S+@\S+\b', re.IGNORECASE)
CAPTION_ALLOWED_RX = re.compile(r'^[^\W_]|[ ,.\'\-()?!:;/&%°"]', re.UNICODE)

# Mirror of SECURITY_TAG_RX in src/prompt-safety.ts: a caption must not be able
# to forge or close one of our own framing tags.
SECURITY_TAG_RX = re.compile(r'</?\s*(untrusted|trusted-peer|scheduled-task|system-reminder)\b[^>]*>',
                             re.IGNORECASE)
STRIPPED = "[stripped]"


def nonce_ok(s):
    return (isinstance(s, str) and NONCE_MIN <= len(s) <= NONCE_MAX
            and all(c.isalnum() and c.isascii() for c in s))


def caption_ok(s):
    if not isinstance(s, str) or not s.strip() or len(s) > CAPTION_MAX:
        return False
    if CAPTION_BANNED_RX.search(s):
        return False
    return all(c.isalnum() or c in " ,.'-()?!:;/&%°\"" for c in s)


def check(doc, mode, labels=None, expected_nonce=None):
    """Returns (findings, cleaned_or_None)."""
    bad = []
    if not isinstance(doc, dict):
        return ["a valasz nem objektum"], None

    unknown = set(doc) - DOC_KEYS
    if unknown:
        bad.append(f"ismeretlen mezo: {sorted(unknown)}")

    nonce = doc.get("nonce")
    if nonce is not None and not nonce_ok(nonce):
        bad.append(f"a nonce csak betu es szam lehet, {NONCE_MIN}-{NONCE_MAX} karakter")
    elif expected_nonce is not None:
        if nonce is None:
            bad.append("--nonce meg lett adva, de a valaszban nincs nonce")
        elif nonce != expected_nonce:
            bad.append("a nonce NEM egyezik: ez egy masik keresre adott valasz")

    if doc.get("mode") != mode:
        bad.append(f"a valasz modja {doc.get('mode')!r}, a kert mod {mode!r}. "
                   f"Ket kulonbozo kockazat, nem keverjuk")

    if doc.get("kind") not in KINDS:
        bad.append(f"kind csak {KINDS} egyike lehet, ez jott: {doc.get('kind')!r}")

    readable = doc.get("readable")
    if not isinstance(readable, bool):
        bad.append("readable KOTELEZO, es logikai ertek. Ha a viewer nem latta a kepet, "
                   "azt mondja meg, ne talaljon ki rola valamit")

    clean = {"mode": mode, "kind": doc.get("kind"), "readable": readable}

    if mode == "match":
        if "caption" in doc:
            bad.append("match modban NINCS caption. Aki leirast ad ott, ahol valasztania "
                       "kellene, az kikerulte a zart listat")
        m = doc.get("matches")
        if not isinstance(m, list):
            bad.append("matches: lista kell (ures is lehet, az azt jelenti hogy egyik cimke sem all)")
            return bad, None
        allowed = set(labels or [])
        seen = set()
        out = []
        for i, e in enumerate(m):
            where = f"matches[{i}]"
            if not isinstance(e, dict) or set(e) != {"label", "confidence"}:
                bad.append(f"{where}: pontosan label es confidence kell")
                continue
            if e["label"] not in allowed:
                # The whole point of this mode. A label nobody offered is the
                # image speaking, not the viewer answering.
                bad.append(f"{where}: a(z) {e['label']!r} cimke NEM szerepelt a felkinaltak "
                           f"kozott. Match modban csak a hivo szavaival lehet valaszolni")
                continue
            if e["confidence"] not in CONFIDENCE:
                bad.append(f"{where}: confidence csak {CONFIDENCE} egyike lehet")
                continue
            if e["label"] in seen:
                bad.append(f"{where}: a(z) {e['label']!r} ketszer szerepel")
                continue
            seen.add(e["label"])
            out.append({"label": e["label"], "confidence": e["confidence"]})
        clean["matches"] = out
    else:
        if "matches" in doc:
            bad.append("describe modban NINCS matches")
        cap = doc.get("caption")
        if not caption_ok(cap):
            bad.append(f"a caption legfeljebb {CAPTION_MAX} karakter, es nem tartalmazhat "
                       f"jelolo karaktert, sortorest, webcimet vagy email cimet")
        else:
            clean["caption"] = SECURITY_TAG_RX.sub(STRIPPED, " ".join(cap.split()))

    if bad:
        return bad, None
    return [], clean


def wrap_untrusted(caption, nonce, source="image"):
    """Same shape as wrapUntrustedFetch in src/prompt-safety.ts.

    The caption is the viewer's words about a stranger's picture. It is data.
    Handing it onward naked -- into a report, a prompt, a message -- is the one
    move that turns this mode from 'as risky as reading a web page' into
    something worse, because a caption reads like our own summary.
    """
    safe_nonce = nonce if nonce_ok(nonce or "") else "none"
    return (f'<untrusted source="image-view:{source}" fetch-nonce="{safe_nonce}">\n'
            f'{caption}\n</untrusted>')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("answer")
    ap.add_argument("--labels", help="match mod: a felkinalt cimkek, vesszovel")
    ap.add_argument("--describe", action="store_true", help="describe mod")
    ap.add_argument("--nonce")
    ap.add_argument("--clean")
    a = ap.parse_args(argv)

    if a.describe and a.labels:
        print("ELUTASITVA: a --describe es a --labels egyszerre nem ertelmes. "
              "Vagy valasztasz a te szavaidbol, vagy leirast kersz.")
        return 1
    if not a.describe and not a.labels:
        print("ELUTASITVA: adj meg --labels listat (match mod) vagy --describe kapcsolot.")
        return 1

    mode = "describe" if a.describe else "match"
    labels = [s.strip() for s in (a.labels or "").split(",") if s.strip()]
    if mode == "match":
        if not labels:
            print("ELUTASITVA: ures cimkelista. Match modban a hivo szavai a valaszkeszlet.")
            return 1
        too_long = [l for l in labels if len(l) > LABEL_MAX]
        if too_long:
            print(f"ELUTASITVA: {LABEL_MAX} karakternel hosszabb cimke: {too_long}")
            return 1

    try:
        with open(a.answer, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"ELUTASITVA: nem olvashato JSON: {e}")
        return 1

    bad, clean = check(doc, mode, labels, a.nonce)
    if bad:
        print("ELUTASITVA. A kepbol jott valasz nem felel meg az urlapnak:")
        for b in bad:
            print(f"  - {b}")
        return 1

    if a.nonce is None:
        print("FIGYELEM: --nonce nelkul futott, tehat NEM ellenoriztem, hogy ez a valasz "
              "a te keresedre jott-e.")
    if not clean["readable"]:
        print("FIGYELEM: a viewer NEM latta rendesen a kepet. Amit mond, keveset er.")

    if mode == "match":
        print(f"RENDBEN. kind={clean['kind']}, {len(clean['matches'])} talalat a felkinalt "
              f"{len(labels)} cimkebol.")
        for m in clean["matches"]:
            print(f"  {m['confidence']:>6}  {m['label']}")
        if not clean["matches"]:
            print("  (egyik cimke sem all ra -- ez ervenyes valasz, nem hiba)")
    else:
        print(f"RENDBEN. kind={clean['kind']}. A leiras ADAT, igy kell tovabbadni:")
        print(wrap_untrusted(clean["caption"], a.nonce))

    if a.clean:
        with open(a.clean, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, ensure_ascii=False, indent=2)
        print(f"  a jovahagyott masolat: {a.clean}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
