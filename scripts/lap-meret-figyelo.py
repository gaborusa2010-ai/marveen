#!/usr/bin/env python3
"""How big are the pages that load on EVERY turn, and how fast are they growing?

WHY IT EXISTS
-------------
The owner asked for this on 2026-09-20 (Telegram msg 3273): "csinald meg rendszeresen,
hogy kordaban tartsuk a novekvo fajljaidat". It exists because these files grow by a
paragraph per lesson learned, silently, and nobody notices until someone measures.

WHAT IT IS HONEST ABOUT, and this matters
-----------------------------------------
Measured 2026-09-20: CLAUDE.md was about 7% of a 310k-token turn, and the evening's trim
(68 627 -> 58 291 bytes) bought roughly 1% per turn. **The loaded pages are NOT the main
token cost** -- the bulk of a turn is the accumulated conversation. So this watcher keeps
the pages readable and bounded; it does not "cut the bill in half", and a future reader
should not expect it to.

THE METHOD IT ENFORCES: rule stays, story moves
------------------------------------------------
Every lesson on these pages has an imperative ("do X, not Y") and a case history ("here is
the measurement that taught us"). The imperative must stay where it fires. The story goes
to the companion file next to it, which is only opened when someone asks why.

**AND THE STEP THAT MAKES IT SAFE:** after trimming, grep the page for every imperative
that was there before. On 2026-09-20 that check raised six "missing" flags and FIVE were
the search being wrong (accents, capitalisation), not the rule being gone. So: when a
check says a rule vanished, re-run it with the exact casing before believing it.

Usage:
  lap-meret-figyelo.py            # JSON
  lap-meret-figyelo.py --ember    # human-readable, with the growth since last time
Exit 0 = nothing over threshold, 1 = something grew past its limit, 2 = error.
"""
import argparse, json, os, sys, time

REPO = "/home/gabor/marveen"
ALLAPOT = os.path.join(REPO, "store", "lap-meretek.json")

# (ut, hatar_bajt, mikor_toltodik_be)
#
# A HATAR NEM IDEAL, HANEM NOVEKEDES-DROT: a 2026-09-20-i merethez van szabva, kis
# tartalekkal. Nem azt mondja meg, mekkora LEGYEN egy lap, hanem azt, hogy ELMOZDULT-E
# azota erdemben. Egy kitalalt ideal-plafon ugyanaz a hiba lenne, amit a CLAUDE.md a
# kitalalt szamokrol mond -- eloszor a skill-indexnek 8000-et adtam FEJBOL, es rogton
# hamis riasztast adott egy lapra, ami semmit nem valtozott.
LAPOK = [
    (os.path.join(REPO, "CLAUDE.md"), 62000, "MINDEN fordulóban"),
    ("/home/gabor/.claude/scheduled-tasks/folyamatos-ellenorzes/SKILL.md", 11000, "felorankent, es bent marad"),
    ("/home/gabor/.claude/scheduled-tasks/memoria-heartbeat/SKILL.md", 6000, "orankent"),
    ("/home/gabor/.claude/scheduled-tasks/kanban-audit/SKILL.md", 13000, "naponta negyszer"),
    ("/home/gabor/.claude/scheduled-tasks/heti-mentes/SKILL.md", 10000, "naponta este"),
    ("/home/gabor/.claude/scheduled-tasks/esti-kanban-atnezes/SKILL.md", 20000, "naponta este"),
    ("/home/gabor/.claude/scheduled-tasks/munkanaplo-zaras-orzo/SKILL.md", 21000, "hetkoznap"),
    (os.path.join(REPO, ".claude", "skills", ".skill-index.md"), 14000, "skill-valasztaskor"),
]


def nev(ut):
    """A megjelenitett nev. Nyolc `SKILL.md` egymas alatt nem informacio."""
    alap = os.path.basename(ut)
    if alap in ("SKILL.md", ".skill-index.md"):
        return os.path.basename(os.path.dirname(ut)) + "/" + alap
    return alap


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ember", action="store_true")
    a = ap.parse_args(argv)

    try:
        with open(ALLAPOT, encoding="utf-8") as fh:
            regi = json.load(fh)
    except (OSError, ValueError):
        regi = {}
    regi_meret = regi.get("meretek") or {}

    sorok, tullepes = [], []
    for ut, hatar, mikor in LAPOK:
        try:
            m = os.path.getsize(ut)
        except OSError:
            # Egy HIANYZO lap nem "nulla bajt": jelentes, nem csend. Kulonben egy
            # atnevezes ugy nez ki, mint egy sikeres karcsusitas.
            sorok.append({"lap": ut, "hiba": "nincs meg a fajl", "mikor": mikor})
            continue
        volt = regi_meret.get(ut)
        sorok.append({"lap": ut, "bajt": m, "hatar": hatar, "mikor": mikor,
                      "elozo": volt, "valtozas": (m - volt) if volt is not None else None,
                      "tullepve": m > hatar})
        if m > hatar:
            tullepes.append((ut, m, hatar))

    ki = {"ido": time.strftime("%Y-%m-%d %H:%M"), "lapok": sorok,
          "meretek": {s["lap"]: s["bajt"] for s in sorok if "bajt" in s}}
    tmp = ALLAPOT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ki, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, ALLAPOT)

    if not a.ember:
        print(json.dumps(ki, ensure_ascii=False))
        return 1 if tullepes else 0

    print(f"BETOLTODO LAPOK -- {ki['ido']}\n")
    print(f"{'bajt':>7} {'hatar':>7} {'valtozas':>9}  lap")
    for s in sorok:
        if "hiba" in s:
            print(f"{'?':>7} {'?':>7} {'':>9}  {nev(s['lap'])}  <-- {s['hiba']}")
            continue
        v = s["valtozas"]
        vs = "-" if v is None else (f"{v:+d}" if v else "0")
        jel = "  <-- TULLEPTE" if s["tullepve"] else ""
        print(f"{s['bajt']:>7} {s['hatar']:>7} {vs:>9}  {nev(s['lap'])}{jel}")
    print()
    if tullepes:
        print("TEENDO a tullepett lapokon: SZABALY marad, TORTENET megy a melette fekvo")
        print("BUKTATOK.md-be vagy a docs/CLAUDE-ESETTORTENET.md-be. Utana KOTELEZO")
        print("visszamerni MINDEN parancs-alakot -- es ha egy 'hianyzik', eloszor a sajat")
        print("keresesed irasmodjat nezd meg, mert ot esetbol negy az.")
    else:
        print("Egyik lap sincs a hatara felett. Nincs teendo.")
    return 1 if tullepes else 0


if __name__ == "__main__":
    sys.exit(main())
