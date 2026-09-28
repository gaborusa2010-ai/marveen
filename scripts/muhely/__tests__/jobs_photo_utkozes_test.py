#!/usr/bin/env python3
"""jobs.py photo: ket foto UGYANABBAN a masodpercben ket KULON fajl legyen.

Miert kell ez a teszt. A masolat neve `job<id>-<masodperc>.jpg` volt, tehat egy
cikluson belul a masodik foto felulirta volna az elsot, hibauzenet nelkul: a
2026-09-16-i visszamenoleges besorolasnal a 09-04-i napon egyedul 17 foto allt,
es a vegen EGY fajl allt volna 17 helyett, kozben a naploban 17 bejegyzes
mutatott volna ra. Nema adatvesztes, zold kimenettel.

A javitas kod lett, a vedelme nem -- Marveen vette eszre, es igaza volt: ugyanaz
a hiany, mint amit aznap delelott en fogalmaztam meg nala.

AZ IDO FIX, es ez a teszt magja. Ket gyors egymas utani hivas VALOSZINULEG
ugyanabba a masodpercbe esik, de nem biztosan -- egy masodperc-atlepes eseten a
teszt zoldet adna anelkul, hogy az utkozest egyaltalan kiprobalta volna. Ezert a
modul `time` nevet cserelem le, nem a sebessegre bizom.

A fa MASOLAT, az eles ledgerhez semmi nem nyul.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
FIX_IDO = 1789540000.0

hiba = 0


def ok(s):
    print(f"  ok   {s}")


def bad(s):
    global hiba
    print(f"  FAIL {s}")
    hiba = 1


class FixTime:
    """Csak azt a ket nevet adja vissza, amit a jobs.py hasznal."""
    def time(self):
        return FIX_IDO

    def __getattr__(self, n):
        import time as _t
        return getattr(_t, n)


def main():
    tmp = tempfile.mkdtemp()
    try:
        sandbox = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(sandbox, "scripts", "muhely"))
        os.makedirs(os.path.join(sandbox, "store"))
        for f in ("jobs.py", "exif.py"):
            shutil.copy(os.path.join(REPO, "scripts", "muhely", f),
                        os.path.join(sandbox, "scripts", "muhely", f))
        # Az exif.py azert jon vele, mert nelkule a futas kiirt egy
        # "No module named 'exif'" sort: elesben a jobs.py a sajat mappajat
        # beszurja a path-ba, a sandboxban nem volt mit betolteni. Az utkozes-merest
        # ez nem befolyasolta, de egy kesobbi olvasonak HIBANAK latszik egy olyan
        # sor, ami nem az. Marveen vette eszre a teszt kimenetén, 2026-09-16.

        ledger = os.path.join(sandbox, "store", "muhely-jobs.json")
        json.dump([{"id": 1, "client": "T", "job": "T", "address": "",
                    "started_at": FIX_IDO - 9 * 3600, "stopped_at": FIX_IDO - 3600,
                    "rate": 200.0, "photos": [], "notes": []}],
                  open(ledger, "w"))

        forras = []
        for i in range(2):
            p = os.path.join(tmp, f"foto{i}.jpg")
            with open(p, "wb") as fh:
                fh.write(b"\xff\xd8\xff" + bytes([i]) * 64)   # ket KULONBOZO tartalom
            forras.append(p)

        spec = importlib.util.spec_from_file_location(
            "jobs_sandbox", os.path.join(sandbox, "scripts", "muhely", "jobs.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.time = FixTime()          # <- az ido fix, nem a sebessegre bizzuk

        class A:
            pass

        for p in forras:
            a = A()
            a.file, a.note, a.id, a.hely = p, "", 1, False
            rc = m.cmd_photo(a)
            if rc not in (0, None):
                bad(f"cmd_photo exit {rc}")

        rows = json.load(open(ledger))
        ph = rows[0].get("photos") or []

        if len(ph) == 2:
            ok("ket bejegyzes keletkezett")
        else:
            bad(f"{len(ph)} bejegyzes keletkezett, nem 2")

        utak = [e["path"] for e in ph]
        if len(set(utak)) == 2:
            ok("a ket bejegyzes KULON fajlra mutat")
        else:
            bad(f"ugyanarra a fajlra mutat: {utak}")

        hianyzo = [u for u in utak if not os.path.exists(u)]
        if not hianyzo:
            ok("mindket fajl letezik a lemezen")
        else:
            bad(f"hianyzo fajl: {hianyzo}")

        # A TARTALOM a dontо: ket kulon nev is allhat egy felulirt fajlra, ha a
        # masolas sorrendje rossz. Itt a ket forras bajtra kulonbozik.
        tartalmak = []
        for u in utak:
            if os.path.exists(u):
                tartalmak.append(open(u, "rb").read())
        if len(set(tartalmak)) == 2:
            ok("a ket fajl tartalma KULONBOZO (nincs felulirás)")
        else:
            bad("a ket fajl tartalma azonos: az egyik felulirta a masikat")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("MIND OK" if not hiba else "VOLT BUKO")
    return hiba


if __name__ == "__main__":
    sys.exit(main())
