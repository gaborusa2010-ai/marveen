#!/usr/bin/env python3
"""
Can the closing watch say BOTH answers, and does it hold its state honestly?

A sentry that only ever says "nothing to report" looks identical to one that is
broken, so every case below is checked in both directions: a log that must
produce a message, and a log that must not.

Nothing here touches the live ledger or sends anything: the module constants are
redirected at a scratch copy and the sender is replaced.
"""
import datetime
import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "munkanaplo-zaras-watch.py")

spec = importlib.util.spec_from_file_location("zaras_watch", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        fails.append(name)


def with_ledger(rows, tmpdir, name="ledger.json"):
    p = os.path.join(tmpdir, name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    mod.LEDGER = p
    return p


def reset_state(tmpdir):
    mod.STATE = os.path.join(tmpdir, "seen.json")
    if os.path.exists(mod.STATE):
        os.unlink(mod.STATE)


def run(argv, sent_box, ok=True):
    sent_box.clear()

    def fake_send(text):
        sent_box.append(text)
        return ok

    real = mod.send
    mod.send = fake_send
    try:
        return mod.main(argv)
    finally:
        mod.send = real


def main():
    now = time.time()
    sent = []
    with tempfile.TemporaryDirectory() as td:
        print("1. nincs nyitott naplo -> csend")
        with_ledger([{"id": 1, "started_at": now - 40000, "stopped_at": now - 1000}], td)
        reset_state(td)
        rc = run(["--notify", "--no-hiany"], sent)
        check("exit 0", rc == 0, f"rc={rc}")
        check("nem kuldott", not sent, f"sent={sent}")

        print("2. nyitott naplo, de meg friss -> csend")
        with_ledger([{"id": 2, "client": "X", "job": "y",
                      "started_at": now - 3600, "stopped_at": None}], td)
        reset_state(td)
        rc = run(["--notify", "--no-hiany"], sent)
        check("exit 0", rc == 0, f"rc={rc}")
        check("nem kuldott", not sent)

        print("3. nyitott naplo 10 oraja -> KULD")
        with_ledger([{"id": 3, "client": "Palmer", "job": "muhely",
                      "started_at": now - 10 * 3600, "stopped_at": None}], td)
        reset_state(td)
        rc = run(["--notify", "--no-hiany"], sent)
        check("exit 0", rc == 0, f"rc={rc}")
        check("kuldott", len(sent) == 1, f"sent={len(sent)}")
        check("benne a job id", sent and "#3" in sent[0])
        check("mindket nyilvantartas emlitve", sent and "appban" in sent[0])
        check("allapot commitolva", os.path.exists(mod.STATE))

        print("4. ugyanaz a munka azonnal ujra -> csend (ismetles-vedelem)")
        rc = run(["--notify", "--no-hiany"], sent)
        check("exit 0", rc == 0, f"rc={rc}")
        check("nem kuldott ujra", not sent)

        print("5. MAS munka nyilt meg -> KULD, a regi allapot ellenere")
        with_ledger([{"id": 4, "client": "Palmer", "job": "masik",
                      "started_at": now - 9 * 3600, "stopped_at": None}], td)
        rc = run(["--notify", "--no-hiany"], sent)
        check("kuldott", len(sent) == 1, f"sent={len(sent)}")
        check("az uj id", sent and "#4" in sent[0])

        print("6. bukott kezbesites -> NEM commitol, es a kovetkezo kor ujra probal")
        with_ledger([{"id": 5, "client": "Palmer", "job": "harmadik",
                      "started_at": now - 9 * 3600, "stopped_at": None}], td)
        reset_state(td)
        rc = run(["--notify", "--no-hiany"], sent, ok=False)
        check("exit 2", rc == 2, f"rc={rc}")
        check("probalta", len(sent) == 1)
        check("allapot NEM keszult", not os.path.exists(mod.STATE))
        rc = run(["--notify", "--no-hiany"], sent)
        check("masodikra kuld", len(sent) == 1)
        check("most mar commitolt", os.path.exists(mod.STATE))

        print("7. serult naplo -> exit 2, nem nema nulla")
        p = os.path.join(td, "rossz.json")
        open(p, "w").write("{ nem json")
        mod.LEDGER = p
        rc = run(["--notify", "--no-hiany"], sent)
        check("exit 2", rc == 2, f"rc={rc}")
        check("nem kuldott", not sent)

        print("8. hianyzo naplo-fajl -> exit 2 (a hiany nem ugyanaz, mint az ures)")
        mod.LEDGER = os.path.join(td, "nincs-ilyen.json")
        rc = run(["--notify", "--no-hiany"], sent)
        check("exit 2", rc == 2, f"rc={rc}")

        print("9. hianyzo nap, nyitott rekord nelkul -> KULD (a masik kerdes)")
        rows = []
        today = datetime.date.today()
        # fill every weekday of the last 14 days EXCEPT one, so exactly one is missing
        # The gap is deliberately in the MIDDLE of the window, not yesterday:
        # an open record that started "10 hours ago" lands on yesterday's date
        # and would fill the gap itself, which is how the first draft of this
        # case passed for the wrong reason.
        hianyzo = None
        jid = 100
        for back in range(1, 15):
            d = today - datetime.timedelta(days=back)
            if d.isoweekday() == 7:
                continue
            if hianyzo is None and back >= 8:
                hianyzo = d
                continue
            start = datetime.datetime(d.year, d.month, d.day, 7, 0).timestamp()
            rows.append({"id": jid, "client": "Palmer", "job": "m",
                         "started_at": start, "stopped_at": start + 9 * 3600})
            jid += 1
        with_ledger(rows, td)
        reset_state(td)
        seen = os.path.join(td, "hiany-seen.json")
        os.environ["MUHELY_NAPLO_HIANY_SEEN"] = seen
        rc = run(["--notify"], sent)
        check("kuldott", len(sent) == 1, f"sent={len(sent)}")
        check("a hianyzo nap benne van", sent and hianyzo.isoformat() in sent[0],
              f"vart={hianyzo}")
        check("nincs nyitott rekord emlitve", sent and "meg fut" not in sent[0])

        print("10. ugyanaz a nap masodszor, ugyanazon a napon -> csend")
        rc = run(["--notify"], sent)
        check("exit 0", rc == 0, f"rc={rc}")
        check("nem kuldott ujra", not sent, f"sent={sent}")

        print("11. --no-hiany -> a masodik kerdes ki van kapcsolva")
        reset_state(td)
        rc = run(["--notify", "--no-hiany"], sent)
        check("exit 0", rc == 0, f"rc={rc}")
        check("nem kuldott", not sent, f"sent={sent}")

        print("12. hianyzo nap ES nyitott rekord -> EGY uzenet, mindketto benne")
        rows.append({"id": 900, "client": "Palmer", "job": "nyitott",
                     "started_at": time.time() - 10 * 3600, "stopped_at": None})
        with_ledger(rows, td)
        reset_state(td)
        rc = run(["--notify"], sent)
        check("egy uzenet", len(sent) == 1, f"sent={len(sent)}")
        check("nyitott rekord benne", sent and "#900" in sent[0])
        check("hianyzo nap is benne", sent and hianyzo.isoformat() in sent[0])
        os.environ.pop("MUHELY_NAPLO_HIANY_SEEN", None)

    # 13. A CSILLAPITO A KERDES TARTALMAT NEZI, NEM CSAK A NAPOT.
    #
    # Forgacs lelete, 2026-09-20: a `hiany_date` a FUTAS napjat tarolta. Ha
    # ugyanazon a napon egy MASIK nap is hianyzani kezdett, a masodik, UJ kerdes
    # nem ment ki, mert a datum mar be volt irva. Harom eset, es a harmadik a
    # NEGATIV KONTROLL -- nelkule egy olyan javitas is atmenne, ami mindig kuld.
    with tempfile.TemporaryDirectory() as td:
        reset_state(td)
        mod.LEDGER = with_ledger([], td)
        real_missing = mod.missing_days
        try:
            kerdes = ["A 2026-09-18 napbol nincs naplo-sor."]
            mod.missing_days = lambda: kerdes[0]

            rc = run(["--notify"], sent)
            check("13a elso kerdes kimegy", len(sent) == 1, f"sent={len(sent)}")
            st1 = json.load(open(mod.STATE, encoding="utf-8"))
            check("13a sha bekerult az allapotba", bool(st1.get("hiany_sha")))

            # UGYANAZ a kerdes ugyanazon a napon -> CSEND (ez a regi viselkedes, marad)
            rc = run(["--notify"], sent)
            check("13b azonos kerdes ugyanaznap -> csend", len(sent) == 0, f"sent={len(sent)}")

            # MAS kerdes ugyanazon a napon -> MEGY (ez az uj viselkedes)
            kerdes[0] = "A 2026-09-18 es a 2026-09-19 napbol nincs naplo-sor."
            rc = run(["--notify"], sent)
            check("13c uj kerdes ugyanaznap -> KIMEGY", len(sent) == 1, f"sent={len(sent)}")
            st2 = json.load(open(mod.STATE, encoding="utf-8"))
            check("13c a sha frissult", st2.get("hiany_sha") != st1.get("hiany_sha"))

            # NEGATIV KONTROLL: a masodik kerdes utan ujra ugyanaz -> megint csend
            rc = run(["--notify"], sent)
            check("13d negativ kontroll: megint csend", len(sent) == 0, f"sent={len(sent)}")
        finally:
            mod.missing_days = real_missing

    print()
    if fails:
        print(f"FAIL: {len(fails)} eset -- {', '.join(fails)}")
        return 1
    print("Minden eset atment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
