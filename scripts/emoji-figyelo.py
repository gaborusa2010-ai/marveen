#!/usr/bin/env python3
"""Did an emoji ever reach us from the owner on Telegram?

WHY THIS EXISTS
---------------
The owner asked on 2026-09-20 (Telegram msg 3219) whether we see emojis yet, and
then whether a thumbs-up reached us (msg 3230). Measured that evening:

  * REACTIONS cannot arrive at all. The telegram plugin registers no
    `message_reaction` handler AND sets no `allowed_updates`, so Telegram never
    even sends the update. Two independent blocks; this script cannot see them
    and does not pretend to.
  * EMOJIS IN MESSAGE TEXT are a different question, and it was OPEN: across 108
    of his messages captured in our transcripts, zero contained one. That zero
    has two possible causes -- they are stripped somewhere, or he simply never
    sent one -- and the same zero marks both.

This watcher closes that question WITHOUT asking him to run tests: it scans the
transcripts for incoming Telegram messages, and reports the first one carrying an
emoji. Until one appears it stays silent, and the denominator keeps growing,
which is itself the useful number.

WHAT IT DOES NOT MEASURE
------------------------
Whether an emoji he sent was stripped. A negative here is still two-valued. What
it CAN say with certainty is the positive: the moment one arrives, delivery works.

Usage:
  emoji-figyelo.py                  # JSON
  emoji-figyelo.py --ember          # human-readable
  emoji-figyelo.py --jelentve 3326  # mark that id as already reported to the owner
Exit 0 = nothing NEW to report (the quiet case), 1 = an unreported emoji message
exists, 2 = error.

WHY THE TWO STEPS (added 2026-09-21, right after the first hit)
--------------------------------------------------------------
The first emoji arrived (msg 3326) and was reported. Without a reported-set this
watcher would then exit 1 on EVERY later run, and the caller page says "exit 1 ->
tell him" -- so a closed question would have produced a report every 30 minutes
forever. That is a check that can no longer say no.

The ack is a SEPARATE run on purpose. If the script marked a find reported in the
same run that surfaced it, a round that died between measuring and sending would
consume the only signal silently. Repeating a report costs one line; losing one
costs the finding.
"""
import argparse, glob, json, os, re, sys

TRANSCRIPTS = "/home/gabor/marveen/.channels-config/projects/-home-gabor-marveen"
ALLAPOT = "/home/gabor/marveen/store/emoji-figyelo.json"

# Pictographs, symbols and dingbats. Deliberately NOT \u2190-\u21ff (arrows): the
# terminal chrome in our own transcripts uses those, and they are not emoji.
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")

# The stored shape is JSON-escaped inside the .jsonl, so the quotes are \" here.
BEJOVO = re.compile(
    r'<channel source=\\"plugin:telegram[^>]*?message_id=\\"(\d+)\\"[^>]*?>\\n(.*?)\\n</channel>'
)


def vizsgal():
    # DEDUP A message_id-RE (2026-09-21). Egy bejovo <channel> blokk TOBB transzkript-
    # fajlban is ott all: egy ujrainduláskor a kontextus atjatszodik az uj fajlba. A regi
    # valtozat minden peldanyt kulon szamolt, igy a 3326-os hupalec "2 uzenetben"-kent
    # jelent meg, es a `bejovo_uzenet` osszeg is fel volt fujva. Ez a torzitas a
    # kontextus-kuszob 300 ezerre vitelevel (ugyanaznap) MEGNo, mert naponta tobbszor
    # indul uj munkamenet -- tehat a szam egyre rosszabb lett volna, eszrevetlenul.
    latott, talalatok = {}, []
    fajlok = glob.glob(os.path.join(TRANSCRIPTS, "*.jsonl"))
    for f in fajlok:
        try:
            sz = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in BEJOVO.finditer(sz):
            mid, torzs = m.group(1), m.group(2)
            if mid in latott:
                continue
            latott[mid] = True
            e = EMOJI.findall(torzs)
            if e:
                talalatok.append({"message_id": mid,
                                  "emoji": "".join(dict.fromkeys(e)),
                                  "reszlet": torzs[:80]})
    return len(fajlok), len(latott), talalatok


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ember", action="store_true")
    ap.add_argument("--jelentve", nargs="+", metavar="MSGID",
                    help="message_id(k), amirol MAR szoltam a gazdanak -- ezek "
                         "utan nem ad tobbet exit 1-et")
    a = ap.parse_args(argv)

    if not os.path.isdir(TRANSCRIPTS):
        print(f"HIBA: nincs meg a transzkript-mappa: {TRANSCRIPTS}", file=sys.stderr)
        return 2
    fajlok, osszes, talalatok = vizsgal()

    # A POZITIV KONTROLL. Enelkul egy elromlott regexp UGYANAZT a nullat adja,
    # mint egy valodi "nem jott emoji" -- pontosan az a csapda, ami miatt ez a
    # szkript letezik. Ha NULLA bejovo uzenetet talalunk, a meres romlott el.
    if osszes == 0:
        print("HIBA: nulla bejovo Telegram-uzenetet talaltam. Ez nem lelet, hanem "
              "elromlott meres (mintaillesztes vagy utvonal).", file=sys.stderr)
        return 2

    ki = {"bejovo_uzenet": osszes, "transzkript_fajl": fajlok,
          "emojis_uzenet": len(talalatok), "talalatok": talalatok[:5]}

    regi = {}
    try:
        with open(ALLAPOT, encoding="utf-8") as fh:
            regi = json.load(fh)
    except (OSError, ValueError):
        pass
    ki["elozo_bejovo"] = regi.get("bejovo_uzenet")
    ki["uj_ota"] = osszes - (regi.get("bejovo_uzenet") or 0)

    jelentett = set(regi.get("jelentett") or [])
    if a.jelentve:
        jelentett |= set(a.jelentve)
    ki["jelentett"] = sorted(jelentett, key=lambda x: (len(x), x))
    ujak = [t for t in talalatok if t["message_id"] not in jelentett]
    ki["uj_talalat"] = len(ujak)

    tmp = ALLAPOT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ki, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, ALLAPOT)

    if not a.ember:
        print(json.dumps(ki, ensure_ascii=False))
    elif ujak:
        print(f"UJ EMOJI: {len(ujak)} uzenetben (osszes talalat {len(talalatok)}, "
              f"{osszes} bejovobol).")
        for t in ujak[:5]:
            print(f"  msg {t['message_id']}: {t['emoji']}  | {t['reszlet']}")
    elif talalatok:
        print(f"Nincs UJ emoji. Mar jelentve: {len(talalatok)} uzenet "
              f"({', '.join(t['message_id'] for t in talalatok[:5])}), "
              f"{osszes} bejovobol.")
    else:
        print(f"Meg egy emoji sem jott at. Bejovo uzenet osszesen: {osszes} "
              f"({fajlok} transzkript-fajlbol). A REAKCIOKAT ez nem meri: azokat "
              f"a Telegram el sem kuldi nekunk.")
    return 1 if ujak else 0


if __name__ == "__main__":
    sys.exit(main())
