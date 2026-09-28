#!/usr/bin/env python3
"""The owner's recurring subscriptions: what renews when, and what it costs a month.

WHERE IT CAME FROM
------------------
Not from us. The owner ran a "Morning Briefing" project in the Claude app that kept this
list, and on 2026-08-05 he confirmed the nine entries himself -- the spec calls them
"ground truth unless he corrects it again". He copied that project into the shared folder
on 2026-09-13 and asked for it on 2026-09-20 (Telegram msg 3187: "A Morning briefing-et
vedd fel ma"). So this file adopts the DATA and the arithmetic rules; it does not rebuild
the HTML page that briefing produced, because we already send a morning briefing.

WHAT IT MEASURED ON DAY ONE, and why the owner needed to know
-------------------------------------------------------------
Seven of the nine renewal dates were already in the past: Twelfthmagpie by 44 days,
Jobescape 29, Rule 1 Investing 22, n8n 19, ChatGPT Plus 18, Omaze 18, Amazon Prime 14.
The old briefing was supposed to roll each one forward on every run. Nobody ran it, so
nothing rolled, and the list quietly stopped describing reality.

WHAT THIS SCRIPT IS NOT
-----------------------
It is NOT bank data. Every figure here is the owner's own 2026-08-05 statement, not a
statement line. If a subscription was cancelled, or a price changed, this file cannot know
-- it will happily roll a dead subscription forward forever. That is why --ember prints the
source date next to the total: a number whose provenance is invisible gets trusted like a
measurement, and this one is a recollection.

THE ARITHMETIC, taken from the original spec because it had already been tested there
-------------------------------------------------------------------------------------
A "every 4 weeks" charge is NOT a monthly charge. There are 365.25/7/4 = 13.04 such periods
in a year, not 12, so treating one as monthly understates the year by about 8.7 percent.
The monthly-equivalent is (amount * 365.25/7/4) / 12. Yearly items divide by 12 the obvious
way. Everything is normalised to an ANNUAL figure first and divided once, so the monthly and
yearly totals always reconcile.

Usage
-----
  elofizetes-figyelo.py            # JSON: minden tetel, napok a megujulasig, osszegek
  elofizetes-figyelo.py --ember    # a gazda ele teheto alak
  elofizetes-figyelo.py --gorgetes # a lejart datumokat elore gorgeti ES KIIRJA a fajlba
  elofizetes-figyelo.py --napok 7  # csak a kovetkezo N napban esedekesek (szurt --ember)

Exit code: 0 ha nincs kozelgo vagy lejart tetel, 1 ha van. A 2 a hibae.
"""
import argparse
import datetime
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAT = os.path.join(REPO, "store", "elofizetesek.json")

# 365.25/7/4: a szokoevet is beleszamolva ennyi negyhetes periodus van egy evben.
NEGYHETES_PER_EV = 365.25 / 7 / 4


def evesitett(tetel, usd_gbp):
    """A tetel EVES koltsege fontban. Minden osszegzes ezen keresztul megy."""
    ar = float(tetel["osszeg"])
    if tetel.get("penznem") == "USD":
        ar *= usd_gbp
    ciklus = tetel["ciklus"]
    if ciklus == "ev":
        return ar
    if ciklus == "ho":
        return ar * 12
    if ciklus == "4het":
        return ar * NEGYHETES_PER_EV
    raise ValueError(f"ismeretlen ciklus: {ciklus!r}")


def kovetkezo(datum, ciklus):
    """Egy szamlazasi ciklussal elore. A honap-hossz szelen csonkol, nem csuszik at."""
    if ciklus == "4het":
        return datum + datetime.timedelta(weeks=4)
    if ciklus == "ev":
        try:
            return datum.replace(year=datum.year + 1)
        except ValueError:  # februar 29
            return datum.replace(year=datum.year + 1, day=28)
    if ciklus == "ho":
        ho = datum.month + 1
        ev = datum.year + (ho - 1) // 12
        ho = (ho - 1) % 12 + 1
        # 31-ei terheles egy 30 napos honapban: a honap utolso napja, nem a kovetkezo else.
        nap = datum.day
        while nap > 0:
            try:
                return datetime.date(ev, ho, nap)
            except ValueError:
                nap -= 1
    raise ValueError(f"ismeretlen ciklus: {ciklus!r}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ember", action="store_true", help="a gazda ele teheto alak")
    ap.add_argument("--gorgetes", action="store_true",
                    help="a lejart datumokat elore gorgeti ES kiirja a fajlba")
    ap.add_argument("--napok", type=int, default=None,
                    help="csak a kovetkezo N napban esedekesek (a lejartak mindig bent vannak)")
    a = ap.parse_args(argv)

    try:
        with open(ADAT, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"HIBA: az adatfajl nem olvashato ({e})", file=sys.stderr)
        return 2

    usd_gbp = float(d.get("usd_gbp_tartalek", 0.74))
    ma = datetime.date.today()
    tetelek, valtozott = [], False

    for t in d["elofizetesek"]:
        # LEMONDVA: benne marad a fajlban, de sem az osszegbe, sem a listaba nem szamit.
        #
        # Nem toroljuk, es ennek oka van. A fajl sajat figyelmeztetese szerint ez NEM banki
        # adat, tehat egy lemondas is EMLEKEZET -- ha kesobb kiderul, hogy megis terheltek,
        # a torolt sor nyomtalan lenne, a megjelolt nem. Es ha ujra elofizet, a tortenet itt
        # all. A `lemondva_forras` mezo orzi, KI es MIKOR mondta.
        #
        # Merve 2026-09-20: a fajl letrehozasa utan HET perccel jott az elso ket lemondas
        # (n8n, Jobescape) -- vagyis a "ez nem banki adat" figyelmeztetes nem elmeleti volt.
        if t.get("lemondva"):
            continue
        dt = datetime.date.fromisoformat(t["next_renewal"])
        gorgetve = 0
        if a.gorgetes:
            # A MULTBA gorgetes nem hiba, hanem az elmaradt futasok potlasa: ha harom
            # honapig senki nem futtatta, harmat kell lepni, nem egyet.
            while dt < ma:
                dt = kovetkezo(dt, t["ciklus"])
                gorgetve += 1
            if gorgetve:
                t["next_renewal"] = dt.isoformat()
                valtozott = True
        tetelek.append({
            "nev": t["nev"], "osszeg": t["osszeg"], "penznem": t.get("penznem", "GBP"),
            "ciklus": t["ciklus"], "next_renewal": dt.isoformat(),
            "napok": (dt - ma).days, "eves_gbp": round(evesitett(t, usd_gbp), 2),
            "gorgetve": gorgetve,
        })

    if valtozott:
        tmp = ADAT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, ADAT)

    lemondott = [t for t in d["elofizetesek"] if t.get("lemondva")]
    eves = round(sum(t["eves_gbp"] for t in tetelek), 2)
    havi = round(eves / 12, 2)
    tetelek.sort(key=lambda t: t["napok"])

    mutat = [t for t in tetelek if a.napok is None or t["napok"] <= a.napok]
    lejart = [t for t in tetelek if t["napok"] < 0]

    if not a.ember:
        print(json.dumps({"ma": ma.isoformat(), "havi_gbp": havi, "eves_gbp": eves,
                          "lejart": len(lejart), "tetelek": tetelek}, ensure_ascii=False))
        return 1 if mutat else 0

    if not mutat:
        return 0
    print(f"ELOFIZETESEK -- {ma.isoformat()}")
    print(f"  becsult havi: {havi:.2f} font | eves: {eves:.2f} font ({len(tetelek)} tetel)")
    if lemondott:
        nevek = ", ".join(x["nev"] for x in lemondott)
        print(f"  lemondva, nem szamit bele: {nevek}")
    if lejart:
        print(f"  LEJART MEGUJULASI DATUM: {len(lejart)} tetel")
    print()
    for t in mutat:
        jel = "LEJART" if t["napok"] < 0 else ("MA" if t["napok"] == 0 else f"{t['napok']:+4d} nap")
        ar = f"{t['osszeg']:.2f} {t['penznem']}/{t['ciklus']}"
        g = f"  (gorgetve {t['gorgetve']}x)" if t["gorgetve"] else ""
        print(f"  {jel:>8s}  {t['next_renewal']}  {t['nev']:<30s} {ar}{g}")
    print()
    print(f"  A dijak forrasa: a gazda 2026-08-05-i megerositese, NEM banki adat.")
    print(f"  USD->GBP: {usd_gbp} (tartalek arfolyam)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
