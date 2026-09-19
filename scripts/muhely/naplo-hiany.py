#!/usr/bin/env python3
"""Melyik munkanaprol NINCS naplo-sor, es all-e valahol egy elfelejtett nyitott rekord.

Miert kellett kulon eszkoz ehhez. 2026-09-16-an a hetfoi rekord 48 oran at nyitva
allt, es a KEDD egyaltalan nem kerult be a naploba. Ket orszem nezte a rendszert, es
mindketto ugyanarra a kerdesre nezett: "van-e nyitott naplo". Egy nyitott rekord
LATSZIK, mert a status kiirja. Egy HIANYZO NAP semmilyen kepernyon nem jelenik meg,
tehat az a fajta hiba nema. Ket kulonbozo kerdes, es eddig csak az egyikre volt mero.

Amit ez a szkript NEM allit: hogy a gazda azon a napon dolgozott. Azt nem tudja senki
a naplobol. Egy hianyzo sor KERDES, nem tenyallitas -- ezert lehet nyugtazni, es a
nyugtazott nap tobbe nem jon elo.

    naplo-hiany.py                      # az utolso 14 nap, hetfotol szombatig
    naplo-hiany.py --ember              # a gazda ele teheto alak, parancs-sor nelkul
    naplo-hiany.py --napok 30
    naplo-hiany.py --nyugtaz 2026-09-15 --ok "nem dolgozott, a gazda mondta"

Kilepesi kod: 0 ha nincs mit kerdezni, 1 ha van. A 2 a hibae (hianyzo fajl, rossz datum).

AZ ABLAK 14 NAP, ES NEM 7, ES EZ MERT DONTES. Az elso valtozat het napos volt, es Marveen
vette eszre a bekotesekor: a 2026-09-05-i hianyzo nap tizenegy naposan mar KICSUSZOTT belole,
tehat az alapertelmezett hivas URES kimenetet adott ugyanarra a kerdesre, amire a 14 napos
valaszt adott. Vagyis a het napos ablakban egy hiany NEMAN ELEVUL: senki nem nyugtazta, senki
nem vette fel, csak eltunt.

Azert ingyen a hosszabb ablak, mert a NYUGTAZAS elnemiti a mar megvalaszolt napokat. Zajt
nem a nap KORA termel, hanem a nyugtazatlansag. Es azert epp 14, mert a szamlazas hetente
megy: egy ket hetes ablak ket szamlazasi kort fog at, tehat egy kimaradt nap meg azelott
elokerul, hogy a masodik szamla is elmenne nelkule.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# A ket ut env-valtozobol is johet, hogy a teszt IZOLALT adaton fusson es soha ne
# nyuljon az eles naplohoz. Alapertelmezesben az eles fajl, tehat a hivo oldalan
# semmi nem valtozik.
JOBS = os.environ.get("MUHELY_JOBS") or os.path.join(REPO, "store", "muhely-jobs.json")
SEEN = os.environ.get("MUHELY_NAPLO_HIANY_SEEN") or os.path.join(
    REPO, "store", "muhely-naplo-hiany-nyugtazva.json")

NAPOK = ["hetfo", "kedd", "szerda", "csutortok", "pentek", "szombat", "vasarnap"]


def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def napok_sorai(jobs):
    """datum -> a rajta INDULT rekordok id-jei. A kezdes datuma szamit, nem a zarase:
    egy ejfel utan zarult nap is ahhoz a naphoz tartozik, amelyiken elkezdodott."""
    ki = {}
    for r in jobs:
        ts = r.get("started_at")
        if not ts:
            continue
        nap = datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d")
        ki.setdefault(nap, []).append(r.get("id"))
    return ki


def nyitott_regi(jobs, ora=20):
    """Nyitott rekord, aminek a kezdese ennel regebbi. Nem 24, mert egy 07:00-kor
    indult nap mar aznap 03:00-kor "regi" lenne 20 oraval -- viszont masnap reggel
    7-kor biztosan az. A ket kor kozott ez a kulonbseg."""
    most = datetime.now().timestamp()
    ki = []
    for r in jobs:
        if r.get("stopped_at") is None and r.get("started_at"):
            eltelt = (most - r["started_at"]) / 3600.0
            if eltelt >= ora:
                ki.append((r.get("id"), r["started_at"], eltelt))
    return ki


EKEZETES = {"hetfo": "hétfői", "kedd": "keddi", "szerda": "szerdai",
            "csutortok": "csütörtöki", "pentek": "pénteki", "szombat": "szombati"}


def ember_szoveg(regi, hianyzo):
    """A gazda ele teheto alak. Parancs-sor SOHA nem kerul bele.

    Miert kell kulon kapcsolo, es miert nem a hivo szuri ki. 2026-09-16-an a
    gep-olvashato kimenet valtozatlanul kiment a gazdanak Telegramra, benne a
    `--nyugtaz` parancs-sorral. A hivo utolag kiszedte azt a sort a szovegbol,
    csakhogy egy SZOVEGRE epulo szuro nemán atenged, amint atfogalmazom a sort.
    Egy kapcsolo SZERZODES: amig ez a fuggveny all, a kimenetben nincs parancs.
    """
    sorok = []
    for jid, ts, eltelt in regi:
        d = datetime.fromtimestamp(ts).astimezone()
        sorok.append(f"A #{jid} munkanapló még nyitva van, {d.strftime('%m-%d %H:%M')} "
                     f"óta ({eltelt:.0f} óra). Lezárjam?")
    for kulcs, nev in hianyzo:
        sorok.append(f"Nincs naplóm a {EKEZETES.get(nev, nev)} napról ({kulcs}). "
                     f"Dolgoztál aznap?")
    return "\n".join(sorok)


def cmd_nyugtaz(args):
    try:
        datetime.strptime(args.nyugtaz, "%Y-%m-%d")
    except ValueError:
        print(f"Nem datum: {args.nyugtaz!r}. Kell: 2026-09-15", file=sys.stderr)
        return 2
    d = load(SEEN, {})
    d[args.nyugtaz] = {"ok": args.ok or "", "mikor": datetime.now().astimezone().isoformat(timespec="seconds")}
    with open(SEEN, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2, ensure_ascii=False)
    print(f"Nyugtazva: {args.nyugtaz}" + (f" ({args.ok})" if args.ok else ""))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--napok", type=int, default=14,
                   help="hany napra visszamenoleg (alap: 14)")
    p.add_argument("--ember", action="store_true",
                   help="ember-olvashato kimenet: a gazda ele teheto, parancs-sor nelkul")
    p.add_argument("--nyugtaz", metavar="YYYY-MM-DD", help="egy napot lezar: tobbe nem kerdez ra")
    p.add_argument("--ok", help="a --nyugtaz indoka, a fajlba kerul")
    args = p.parse_args()

    if args.nyugtaz:
        return cmd_nyugtaz(args)

    jobs = load(JOBS, None)
    if jobs is None:
        print(f"Nincs meg a naplo: {JOBS}", file=sys.stderr)
        return 2

    sorok = napok_sorai(jobs)
    nyugtazva = load(SEEN, {})
    ma = datetime.now().astimezone().date()

    hianyzo = []
    for i in range(1, args.napok + 1):          # a MAI nap kimarad: meg tart
        nap = ma - timedelta(days=i)
        if nap.weekday() == 6:                   # vasarnap nem munkanap
            continue
        kulcs = nap.strftime("%Y-%m-%d")
        if kulcs in sorok or kulcs in nyugtazva:
            continue
        hianyzo.append((kulcs, NAPOK[nap.weekday()]))

    regi = nyitott_regi(jobs)

    if not hianyzo and not regi:
        if not args.ember:
            print(f"Rendben: az utolso {args.napok} nap minden munkanapjara van naplo-sor, "
                  f"es nincs regota nyitott rekord.")
        return 0

    if args.ember:
        print(ember_szoveg(regi, hianyzo))
        return 1

    for jid, ts, eltelt in regi:
        kezd = datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M")
        print(f"NYITVA MARADT: #{jid}, kezdes {kezd}, eltelt {eltelt:.1f} ora.")
    if hianyzo:
        print("NINCS NAPLO-SOR (kerdes, nem allitas -- lehet, hogy nem is dolgozott):")
        for kulcs, nev in hianyzo:
            print(f"  {kulcs} ({nev})")
        print("Ha nem dolgozott:  python3 scripts/muhely/naplo-hiany.py --nyugtaz "
              f"{hianyzo[0][0]} --ok \"nem dolgozott\"")
    return 1


if __name__ == "__main__":
    sys.exit(main())
