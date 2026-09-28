#!/usr/bin/env python3
"""Napi naplo bejegyzes: allapot-jeloles kotelezo, ORA PEDIG NINCS.

MIERT NINCS BENNE ORA (Marveen dontese, 2026-09-11, msg 4779): a dashboard MINDEN
bejegyzes melle kiirja a sajat `created_at` mezojet ora:perc alakban
(`renderLogEntries`, web/app.js). Egy kezzel irt prefix tehat nem redundans, hanem
EGY SORBAN ALL A VALODI IDOVEL, es amikor elcsuszik, az olvaso ket kulonbozo idot
lat ugyanabban a sorban.

VISSZAMERVE A SAJAT NAPLOMON (2026-09-11, 21 sor): 5 elteres, es MIND A OT a
kezzel irt sorokbol valo, a legnagyobb 38 perc (a "08:05" valojaban 07:27 volt).
A gepi prefix ota (16 sor) NULLA elteres -- vagyis az eszkoz megoldotta a
csuszast, de a prefix maga igy is folosleges, mert a dashboard kiirja.

AMI MARAD KOTELEZO: a LEZART / FOLYAMATBAN jeloles. A napi naplo APPEND-ONLY,
tehat egy reszeredmeny vegeredmenynek latszik nelkule, es ezt a dashboard NEM
irja ki helyettem.

    python3 scripts/muhely/naplo.py "LEZART" "Tema. Mi tortent, mi lett az eredmeny."
    python3 scripts/muhely/naplo.py "FOLYAMATBAN" - < szoveg.txt

Az elso argumentum az ALLAPOT (LEZART vagy FOLYAMATBAN), mert a sajat szabalyom
szerint egy reszeredmeny vegeredmenynek latszik nelkule. A szoveg johet
argumentumkent vagy stdinrol ("-"), es a stdines alak azert van itt, mert a
shell a backtick-et es a $(...)-t vegrehajtja, mielott a script elindulna.
"""
import json
import os
import sys
import urllib.request

AGENT = "forgacs"
API = "http://localhost:3420/api/daily-log"
TOKEN_FILE = "/home/gabor/marveen/store/.dashboard-token"
ALLAPOTOK = ("LEZART", "FOLYAMATBAN")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    allapot = sys.argv[1].upper()
    if allapot not in ALLAPOTOK:
        sys.exit(f"az allapot {' vagy '.join(ALLAPOTOK)} lehet, nem {sys.argv[1]!r}")
    szoveg = sys.stdin.read().strip() if sys.argv[2] == "-" else " ".join(sys.argv[2:]).strip()
    if not szoveg:
        sys.exit("ures bejegyzes")

    # A PREFIXET AZ ESZKOZ TESZI RA, ES A HIVO IS ODAIRJA. 2026-09-16-an igy lett
    # "[LEZART] [LEZART] ..." egy sorombol. A naplo append-only, tehat az a sor ott
    # marad; ez a nehany karakter viszont megelozi a kovetkezot. Ugyanaz az alak,
    # mint minden kettos kezeles: KET hely tudja jol, es ezert egyik sem teszi meg.
    for a in ALLAPOTOK:
        if szoveg.upper().startswith(f"[{a}]"):
            szoveg = szoveg[len(a) + 2:].lstrip()
            break

    # AZ IDO A GEPTOL JON. Ugyanabbol a forrasbol, amit a `date` is mutat:
    # a helyi zona (Europe/London) a rendszere, nem a sajat fejszamolasom.
    # ORA NINCS: a dashboard a sajat created_at mezojet irja ki minden sor melle.
    sor = f"[{allapot}] {szoveg}"

    with open(TOKEN_FILE) as fh:
        token = fh.read().strip()
    req = urllib.request.Request(
        API,
        data=json.dumps({"agent_id": AGENT, "content": sor}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        valasz = json.loads(r.read() or b"{}")
    if not valasz.get("ok"):
        sys.exit(f"a naplo NEM fogadta el: {valasz}")
    print(f"beirva: {sor[:90]}{'...' if len(sor) > 90 else ''}")


if __name__ == "__main__":
    main()
