#!/usr/bin/env python3
"""
Does the daily window actually differ from the rolling one, and does warn-only warn?

The owner's instruction on 2026-09-16 was specific: "ha eleri a szintet az agent akkor
alljon meg arra a napra, es holnap folytatja". A 24-hour ROLLING window does not do that
-- an agent stopped at 20:00 stays stopped until 20:00 the next day. The anchored window
is the whole point, so it gets a test that can tell the two apart.

Nothing here touches a live agent: the module's project_dir, its stopper and its sender
are all replaced.
"""
import datetime
import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "agent-budget-guard.py")
spec = importlib.util.spec_from_file_location("guard", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

fails = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name} {'' if cond else detail}")
    if not cond:
        fails.append(name)


def write_transcript(d, when, tokens):
    """One assistant line with `tokens` cache-read at `when` (a datetime)."""
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "s.jsonl")
    rec = {"timestamp": when.astimezone(datetime.timezone.utc)
           .strftime("%Y-%m-%dT%H:%M:%S.000Z"),
           "message": {"role": "assistant",
                       "usage": {"cache_read_input_tokens": tokens,
                                 "input_tokens": 0, "output_tokens": 0,
                                 "cache_creation_input_tokens": 0}}}
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    os.utime(p, (time.time(), time.time()))
    return p


def main():
    now = datetime.datetime.now()
    with tempfile.TemporaryDirectory() as td:
        mod.PROJECTS = td
        agent_dir = os.path.join(
            td, mod.project_slug(os.path.join(mod.REPO, "agents", "proba")))

        print("1. day_start a legutobbi 06:00-ra mutat, nem 24 oraval ezelottre")
        ds = datetime.datetime.fromtimestamp(mod.day_start(6))
        check("06:00-kor all", ds.hour == 6 and ds.minute == 0, f"{ds}")
        check("a multban van", ds <= now, f"{ds}")
        check("24 oranal nem regebbi", (now - ds).total_seconds() <= 86400, f"{ds}")

        print("2. A KULONBSEG: egy tetel a MAI 06:00 ELOTT, de 24 oran BELUL")
        # Only constructible when the run happens after 07:00; before that the two
        # windows coincide and the case would silently prove nothing.
        korabban = ds - datetime.timedelta(hours=1)
        if (now - ds).total_seconds() < 3600:
            print("     (kihagyva: 07:00 elott a ket ablak egybeesik, "
                  "az eset nem tudna kulonbseget mutatni)")
        else:
            write_transcript(agent_dir, korabban, 5_000_000)
            gordulo, _, _, _ = mod.window_tokens("proba", 24, 1.0)
            napi, _, _, _ = mod.window_tokens("proba", 24, 1.0, daily_from=6)
            check("a gordulo 24h LATJA", gordulo == 5_000_000, f"{gordulo}")
            check("a napi ablak NEM latja", napi == 0, f"{napi}")

        print("3. a mai 06:00 UTANI tetelt MINDKETTO latja")
        write_transcript(agent_dir, ds + datetime.timedelta(minutes=30), 7_000_000)
        gordulo, _, _, _ = mod.window_tokens("proba", 24, 1.0)
        napi, _, _, _ = mod.window_tokens("proba", 24, 1.0, daily_from=6)
        check("gordulo latja", gordulo == 7_000_000, f"{gordulo}")
        check("napi latja", napi == 7_000_000, f"{napi}")

        print("4. cache-read suly: 0.1 tizedere viszi")
        sulyozott, _, _, _ = mod.window_tokens("proba", 24, 0.1, daily_from=6)
        check("700 000 lett", sulyozott == 700_000, f"{sulyozott}")

        print("5. warn-only: NEM allit le, de KULD")
        kuldve, leallitva = [], []
        mod.notify = lambda m, to_owner=False: (kuldve.append(m), True)[1]
        mod.stop_agent = lambda a: (leallitva.append(a), True)[1]
        mod.load = lambda: {}
        mod.save = lambda d: None
        rc = mod.main(["--agent", "proba", "--cap", "5000000",
                       "--daily-from", "6", "--warn-only"])
        check("exit 0", rc == 0, f"rc={rc}")
        check("kuldott", len(kuldve) == 1, f"{len(kuldve)}")
        check("NEM allitott le", not leallitva, f"{leallitva}")
        check("az uzenet nem allit leallitast", kuldve and "NEM allitott le semmit" in kuldve[0])

        print("6. warn-only NELKUL ugyanaz a helyzet LEALLIT (pozitiv kontroll)")
        kuldve.clear(); leallitva.clear()
        rc = mod.main(["--agent", "proba", "--cap", "5000000", "--daily-from", "6"])
        check("exit 0", rc == 0, f"rc={rc}")
        check("leallitotta", leallitva == ["proba"], f"{leallitva}")

        print("7. kuszob ALATT egyik agon sincs semmi")
        kuldve.clear(); leallitva.clear()
        rc = mod.main(["--agent", "proba", "--cap", "900000000", "--daily-from", "6"])
        check("exit 0", rc == 0, f"rc={rc}")
        check("nem kuldott", not kuldve)
        check("nem allitott le", not leallitva)

        print("8. a fo-agens utja mas: nincs -agents- tag")
        check("marveen utja", mod.project_dir("marveen").endswith(mod.project_slug(mod.REPO)),
              mod.project_dir("marveen"))
        check("sub-agens utja", mod.project_dir("roxy").endswith("-agents-roxy"),
              mod.project_dir("roxy"))

        print("9. hianyzo mappa: NEM nyugodt nulla, hanem exit 3")
        rc = mod.main(["--agent", "nincs-ilyen", "--cap", "1000", "--daily-from", "6"])
        check("exit 3", rc == 3, f"rc={rc}")

        print("10. warn-only ISMETLES: ugyanaz a sav ma tobbet NEM szol")
        # A regresszio, amit ez orzi: a futas-alapu ismetlesvedelem warn-only
        # modban nem fog, mert a fo-agenst senki nem allitja le -- dolgozik
        # tovabb, tehat minden korben "ujra aktiv". Elesben ez 41 perc alatt ot
        # figyelmeztetest jelentett ugyanarrol a szazalekrol (2026-09-16).
        # MASOLATOT adunk vissza, nem magat a dict-et. Az elso valtozat
        # `lambda: allapot` volt, es a `save` elso lepese egy `clear()` -- ami
        # ugyanazt az objektumot uritette ki, amit a hivo eppen ment. Az eredmeny
        # egy orokre ures allapot es negy kimeno uzenet: a teszt a SAJAT
        # duplaja miatt bukott, nem a mert kod miatt.
        allapot = {}
        mod.load = lambda: dict(allapot)
        def ment(d):
            allapot.clear(); allapot.update(d)
        mod.save = ment
        kuldve.clear()
        for _ in range(4):
            # minden korben "friss" aktivitas, ahogy egy dolgozo fo-agensnel
            write_transcript(agent_dir, ds + datetime.timedelta(minutes=30), 7_000_000)
            mod.main(["--agent", "proba", "--cap", "9000000", "--daily-from", "6",
                      "--warn-only"])
        check("negy korbol EGY uzenet", len(kuldve) == 1, f"{len(kuldve)} ment ki")
        check("a sav rogzult", allapot.get("proba", {}).get("warn_levels") == [70],
              f"{allapot.get('proba')}")

        print("11. de ha a helyzet ROMLIK, a kovetkezo sav MEGSZOLAL")
        kuldve.clear()
        write_transcript(agent_dir, ds + datetime.timedelta(minutes=30), 8_600_000)
        mod.main(["--agent", "proba", "--cap", "9000000", "--daily-from", "6",
                  "--warn-only"])
        check("a 95%-os sav szolt", len(kuldve) == 1, f"{len(kuldve)}")
        check("mindket sav rogzult",
              allapot.get("proba", {}).get("warn_levels") == [70, 95],
              f"{allapot.get('proba')}")

        print("12. a kuszob ALATT warn-only sem szol")
        kuldve.clear()
        allapot.clear()
        write_transcript(agent_dir, ds + datetime.timedelta(minutes=30), 1_000_000)
        rc = mod.main(["--agent", "proba", "--cap", "9000000", "--daily-from", "6",
                       "--warn-only"])
        check("exit 0", rc == 0, f"rc={rc}")
        check("nem kuldott", not kuldve, f"{kuldve}")

    print()
    if fails:
        print(f"FAIL: {len(fails)} -- {', '.join(fails)}")
        return 1
    print("Minden eset atment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
