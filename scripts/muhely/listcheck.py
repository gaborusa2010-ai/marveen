#!/usr/bin/env python3
"""
Cut list sanity checker: the rules eighty published plans taught us, in code.

Reading other people's plans for two days produced a short list of mistakes that
appear again and again, in published work and in our own. This runs those checks
over a cut list before it goes to the bench, so the sheet is measured, not trusted.

Usage
-----
  listcheck.py <job.json> [--overall W]

The job file is the same shape cutlist.py takes:

  {"job": "...", "stock": [{"material": "18ML", "w": 2440, "h": 1220}, ...],
   "parts": [{"name": "Gable", "w": 429, "h": 353, "qty": 4,
              "grain": "w", "material": "18ML", "edging": "front edge"}, ...]}

`--overall` is the finished width of the run, if there is one. Given it, the checker
tries to prove the widest parts add up to it.

What it checks, and why each rule exists
----------------------------------------
1  NOT MILLIMETRES        A value under 100 on a panel is nearly always inches left in
                          by mistake. The owner works in mm only.
2  CAD RESIDUE            Three decimals is a calculation remainder, not precision.
                          Round to 0.5: the panel saw cannot do better.
3  OFF THE HALF           Anything not on a 0.5 step cannot be set on the saw.
4  BIGGER THAN THE BOARD  A part longer or wider than its sheet never gets cut.
5  NO GRAIN               Every visible part needs a grain direction. Missing grain is
                          how a door ends up lying the wrong way round.
6  NO EDGING COLUMN       Edging is per EDGE, and each banded edge takes 2mm off THAT
                          dimension. A list without the column is not finished.
7  SUSPICIOUS PAIR        Two parts with the same name and different sizes usually means
                          one of them was edited and the other was not.
8  DOES NOT ADD UP        With --overall, the run has to come out of its own parts.
9  SHELF TOO TIGHT/LOOSE  With --opening, an adjustable shelf wants 1 to 4mm of total
                          clearance. Zero means a fixed shelf (say so), and more than 4
                          usually means it was sized to the wrong opening. Eighty plans
                          put it between 1.6 and 6.4mm total, our own work at 1.5.
10 HOUSED PART TOO SHORT  A part that sits in a groove is WIDER than the clear opening
                          by twice the groove depth. If a row says "horony" or "dado"
                          and is not larger than the opening, it was sized as if it butted.
11 ASSUMED ANGLE         A raked job quoted before the site visit stands on a stair figure
                          nobody measured. On a rake that is not one part wrong, it is EVERY
                          upright wrong. Fine for a quote, refused as a final cut list.
                          A list with NO slope_source is judged on whether it looks raked:
                          silence on a straight job, a question on a raked one.

Exit code is 1 if anything was found, so it can gate a build step.
"""
import json
import sys
from collections import defaultdict


def near_half(v):
    return abs(v * 2 - round(v * 2)) < 1e-6


def check(job, overall=None):
    findings = []
    parts = job.get("parts", [])

    # 11 ASSUMED ANGLE. A raked job quoted before the site visit stands on a stair
    # figure nobody measured. The quote says so; the cut list, three weeks later,
    # is just numbers. On a rake that is not one part wrong, it is every upright
    # wrong. carcass.py marks it, this refuses to let it reach the saw unnoticed.
    # It does NOT block the provisional list a quote needs -- it blocks it going
    # out as final, which is the only place the distinction still matters.
    # The missing key used to fall through this test, so carcass.py called an
    # absent field an assumption while this called it fine: one field, two
    # opposite defaults, and a raked list with no provenance passed clean. It
    # cannot simply flag every list without the key either -- a hand-written job
    # file has no reason to carry one, and a checker that cries wolf gets ignored,
    # which is how the two deliberate false alarms were removed from this file.
    # So: an explicit value is judged on its face, and a SILENT list is judged on
    # whether it looks raked. The rake leaves fingerprints in the rows.
    #
    # KNOWN LIMIT, measured 2026-08-28: this recognition stands on the row TEXT,
    # so renaming the parts to "A panel", "B panel" and dropping the notes hides a
    # raked list from it completely -- 2000 and 1316 tall uprights, and it passes
    # clean. That is not the guarantee and was never meant to be. The guarantee is
    # on the carcass.py side, which now ALWAYS writes slope_source; this heuristic
    # is a net under hand-written files, not the floor.
    #
    # And deliberately NOT a geometric test. Two uprights of very different
    # heights would catch the renamed file, but it is also what a stepped shelf,
    # a corner unit or a run against a sloping ceiling looks like, and a check
    # that fires on every asymmetric design teaches you to ignore it. Two false
    # alarms were already cut out of this file for exactly that reason.
    RAKE_MARKS = ("[feltetelezett szog]", "ferde", "trapez", "hosszu pont")
    looks_raked = any(
        any(m in (str(p.get(k, "")).lower()) for k in ("name", "note") for m in RAKE_MARKS)
        for p in parts)
    src = job.get("slope_source")
    if src is not None and src not in ("mert", "nincs ferde"):
        findings.append(("FELTETELEZETT SZOG", "az egesz lista",
                         "a lepcso szoge FELTETELEZETT, nem mert. Ajanlathoz jo, "
                         "vegleges szabashoz nem: minden allo elem hossza ezen all. "
                         "Merd le a helyszinen, ird at a slope.source mezot 'mert'-re, "
                         "es futtasd ujra"))
    elif src is None and looks_raked:
        findings.append(("ISMERETLEN EREDETU SZOG", "az egesz lista",
                         "a lista ferde munkanak latszik, de nincs benne slope_source, "
                         "tehat nem tudni, merve vagy feltetelezve lett-e a lepcso "
                         "szoge. Vedd fel a mezot: 'mert', vagy ha nem az, akkor "
                         "'feltetelezett'"))
    # `stock` comes in two shapes: a list of per-material entries, or a single
    # dict that applies to every part (which is what carcass.py --cutlist wrote,
    # so this checker used to die on its own pipeline's output). cutlist.py has
    # always accepted both; matching it here keeps one job file valid for both
    # tools. Getting this wrong is worse than a crash when it does not crash:
    # an empty table makes check 4 look at nothing and report clean.
    raw = job.get("stock") or []
    if isinstance(raw, dict) and "material" not in raw:
        # One board size for the whole job: key it to every material in the list,
        # so the lookup below finds it instead of quietly finding nothing.
        stock = {p.get("material"): dict(raw) for p in parts}
    else:
        entries = raw if isinstance(raw, list) else [raw]
        stock = {s.get("material"): s for s in entries}

    # Rows whose w/h are real numbers. Every size-based check below this loop
    # iterates THIS list, not `parts`: one unusable row used to kill the whole
    # run at the first bare float(), four separate places deep (222, 236, 257,
    # 260 before this change). Filtering once is why a new check added later
    # cannot fall into the same hole.
    numeric_parts = []

    for p in parts:
        n = p.get("name", "?")

        if not p.get("grain"):
            findings.append(("NINCS SZALIRANY", n, "lathato alkatresznel kotelezo"))

        # 13 UNUSABLE SIZE. Measured 2026-09-12, on a real proposal: put a row in
        # with `w: "tbc"` (the same placeholder that is a DECISION for edging) and
        # this checker died at float() with a traceback -- and exited 1, the SAME
        # code a clean finding-report exits with. So a caller that reads only the
        # exit code sees a working checker, while every finding after the crash,
        # including the undecided-edging warning the row was added for, is gone.
        # A checker must not be silenceable by its own input.
        #
        # Two kinds, kept apart because the fix differs. A non-numeric size is an
        # OPEN QUESTION written into a number field: name it, and skip the size
        # checks for that row so the rest of the list still gets checked. A zero
        # or negative size is a row that would be nested as nothing.
        dims, unusable = {}, False
        for key in ("w", "h"):
            raw_v = p.get(key)
            try:
                dims[key] = float(raw_v if raw_v is not None else 0)
            except (TypeError, ValueError):
                unusable = True
                findings.append(("NEM SZAM MERET", n,
                                 f"{key}={raw_v!r}: ez nem meret, hanem nyitott kerdes egy "
                                 f"szam helyen. A sor tobbi meret-ellenorzese kimarad, a "
                                 f"listaen nem: dontsd el, mielott a lap a fureszhez megy"))
        if unusable:
            continue
        if dims["w"] <= 0 or dims["h"] <= 0:
            findings.append(("NULLA MERET", n,
                             f"{p.get('w')} x {p.get('h')}: nulla vagy negativ meret, ez a sor "
                             f"semmire sem nestelodne. Ha helykitolto, akkor sincs helye a "
                             f"szabaslistan meret nelkul"))
            continue

        numeric_parts.append(p)

        for key in ("w", "h"):
            v = dims[key]
            # Only both-dimensions-small is a real inch smell. A 60mm plinth strip or a
            # 38mm rail is perfectly normal, and flagging those trains you to ignore the tool.
            if 0 < v < 100 and 0 < dims["w"] < 100 and 0 < dims["h"] < 100 and key == "w":
                findings.append(("NEM MILLIMETER", n,
                                 f"{p.get('w')} x {p.get('h')}: mindket meret 100 alatt, "
                                 f"valoszinuleg hüvelyk maradt benne"))
            if abs(v - round(v, 2)) > 1e-9:
                findings.append(("CAD TIZEDES", n,
                                 f"{key}={v}: harom tizedes, ez szamitasi maradek. Kerekitsd 0,5-re"))
            elif v and not near_half(v):
                findings.append(("NEM FEL MILLIRE", n,
                                 f"{key}={v}: a panelfuresz 0,5 mm-nel pontosabbat nem tud"))

        st = stock.get(p.get("material"))
        if st:
            bw, bh = float(st["w"]), float(st["h"])
            pw, ph = dims["w"], dims["h"]
            if not ((pw <= bw and ph <= bh) or (pw <= bh and ph <= bw)):
                findings.append(("NAGYOBB MINT A TABLA", n,
                                 f"{pw} x {ph} nem fer a {bw} x {bh} tablara"))


    # Edging is a property of the CUT LIST, not of the nesting input. If not one row
    # carries it, say so ONCE: this is either the wrong file or an unfinished list.
    if parts and not any("edging" in p for p in parts):
        findings.append(("NINCS ELSZALAG OSZLOP", "az egesz lista",
                         "egyetlen soron sincs elszalag. Ha ez a SZABASLISTA, akkor nincs kesz: "
                         "az elszalag elenkent megy, es minden szalagos el 2 mm-t vesz le ABBOL a "
                         "meretbol. Ha ez a nestelo bemenete, akkor rendben, ott nincs ra szukseg"))

    # 12 UNDECIDED EDGING. The column check above fires only when the WHOLE list
    # is missing edging. A single row that says "tbc" passes it, and passes every
    # other check too, because "tbc" is a non-empty string: the row looks filled in.
    # Measured 2026-09-09 on the live Lyford B-02 list -- `drawer front 569x150,
    # edging "tbc"`, four of them -- and the list reported clean.
    #
    # Edging is not a note. Each banded edge takes 2 mm off THAT dimension, so an
    # undecided edge is an undecided SIZE, on a sheet that goes to the saw. The
    # danger is exactly that it does not look unfinished.
    #
    # Narrow on purpose: only literal placeholders fire. "none", "none, painted",
    # "2 mm front edge" are decisions and stay silent. A checker that fires on
    # every edging string would be ignored inside a week, and two false alarms
    # were already cut out of this file for that reason.
    PLACEHOLDER = {"tbc", "tbd", "t.b.c.", "t.b.d.", "?", "??", "-", "--",
                   "n/a", "na", "kesobb", "kesobb dontjuk", "meg nem tudom"}
    for p in parts:
        raw = p.get("edging")
        if raw is None:
            continue
        if str(raw).strip().lower() in PLACEHOLDER:
            findings.append(("ELDONTETLEN ELSZALAG", p.get("name"),
                             f"elszalag = \"{raw}\", ez nem dontes. Minden szalagos el 2 mm-t "
                             f"vesz le ABBOL a meretbol, tehat a {p.get('w')}x{p.get('h')} "
                             f"meg nem vegleges. Dontsd el, mielott a lap a fureszhez megy"))

    by_name = defaultdict(set)
    for p in numeric_parts:
        by_name[p.get("name")].add((float(p.get("w", 0)), float(p.get("h", 0))))
    for n, sizes in by_name.items():
        if len(sizes) > 1:
            findings.append(("AZONOS NEV, MAS MERET", n,
                             " es ".join(f"{a}x{b}" for a, b in sorted(sizes))
                             + ": az egyiket atirtak, a masikat nem?"))

    opening = job.get("opening")
    if opening:
        opening = float(opening)
        for p in numeric_parts:
            name = (p.get("name") or "").lower()
            note = (p.get("edging", "") + " " + str(p.get("note", ""))).lower()
            if "polc" in name or "shelf" in name:
                w = float(p.get("w", 0))
                slack = opening - w
                housed = "horony" in note or "dado" in note or "groove" in note
                if housed:
                    if slack >= 0:
                        findings.append(("HORONYBA ULO DARAB TUL ROVID", p.get("name"),
                                         f"{w} egy {opening} nyilasban, de horonyba ul: "
                                         f"NAGYOBBNAK kell lennie a nyilasnal, ketszer a horonymelyseggel"))
                elif slack < 0:
                    findings.append(("POLC SZELESEBB A NYILASNAL", p.get("name"),
                                     f"{w} nem megy be egy {opening} nyilasba"))
                elif slack == 0:
                    findings.append(("POLC JATEK NELKUL", p.get("name"),
                                     f"{w} = a nyilas. Ez FIX polcnal helyes; ha allithato, be fog szorulni"))
                elif slack > 4.0:
                    findings.append(("POLC TUL BO", p.get("name"),
                                     f"{slack:.1f} mm osszes jatek egy {opening} nyilasban. "
                                     f"A tervek 1,6 es 6,4 kozott adjak, a sajat munkank 1,5-et. "
                                     f"Ennyi altalaban azt jelenti, hogy rossz nyilashoz meretezted"))

    if overall:
        widest = max((float(p["w"]) for p in numeric_parts), default=0)
        best = None
        for p in numeric_parts:
            w = float(p["w"])
            for k in range(1, 9):
                if abs(k * w - overall) < 1.0:
                    best = (p.get("name"), k, w)
        if best is None:
            findings.append(("NEM JON KI AZ OSSZMERET", "-",
                             f"a {overall} mm-es futast egyetlen alkatresz sem adja ki egesz szammal; "
                             f"a leghosszabb darab {widest}. Ellenorizd kezzel, hogy a reszek osszege = osszmeret"))
    return findings


def main(argv):
    # `--help` used to fall through to open(argv[1]) and die with a traceback
    # naming "--help" as a missing file. The usage text was always here; only
    # the flag that everyone tries first was not wired to it.
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2 if len(argv) < 2 else 0
    overall = None
    if "--overall" in argv:
        overall = float(argv[argv.index("--overall") + 1])
    with open(argv[1]) as fh:
        job = json.load(fh)

    findings = check(job, overall)
    print(f"LISTA-ELLENORZES: {job.get('job', argv[1])}")
    print(f"  {len(job.get('parts', []))} alkatresz-sor\n")
    if not findings:
        print("  Nincs kifogas. A lista atment minden ellenorzesen.")
        return 0
    groups = defaultdict(list)
    for kind, name, detail in findings:
        groups[kind].append((name, detail))
    for kind in sorted(groups):
        print(f"  {kind}  ({len(groups[kind])} db)")
        for name, detail in groups[kind][:12]:
            print(f"    - {name}: {detail}")
        if len(groups[kind]) > 12:
            print(f"    ... es meg {len(groups[kind]) - 12} sor")
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
