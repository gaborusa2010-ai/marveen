#!/usr/bin/env python3
"""
Does the fleet gate stop the right agents, and can it say both answers?

The case it exists for: every agent under its own cap while the fleet is over
the day's budget. A test that only checks "over the line -> stop" would pass
even if the gate stopped the wrong people, so the cases below pin down WHO.
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "fleet-daily-budget.py")
spec = importlib.util.spec_from_file_location("fleetbudget", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

fails = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name} {'' if cond else detail}")
    if not cond:
        fails.append(name)


class FakeGuard:
    """Stands in for agent-budget-guard: fixed spends, recorded stops."""
    def __init__(self, spends):
        self.spends = spends
        self.lasts = {}
        self.stopped = []
        self.sent = []

    def window_tokens(self, agent, hours, weight, daily_from=None):
        if agent not in self.spends:
            return 0, 0, 0.0, True
        return self.spends[agent], 1, self.lasts.get(agent, 0.0), False

    def stop_agent(self, agent):
        self.stopped.append(agent)
        return True

    def notify(self, msg):
        self.sent.append(msg)
        return True


# A heti ablak "neutral" allasa: merheto, de nem szol bele -- a napi proxy dont.
# 2026-10-11 ota (owner msg 7253) a NEM merheto heti szam mellett a kapu NEM allit
# le, ezert az 1-9. eset, ami a napi proxyt meri, egy semleges, MERT heti szamot kap.
SEMLEGES = {"window": "seven_day", "used_pct": 80.0, "elapsed_pct": 50.0,
            "resets_at": 0, "hours_left": 84.0, "age_min": 1.0}
_ALAP = object()


def run(spends, argv, allapot, weekly=_ALAP, lasts=None):
    """Egy futas, rogzitett heti ablakkal.

    A heti ablakot MINDIG rogzitjuk: kulonben a teszt a GEP valodi pillanatkepet
    olvasna -- vagyis napszaktol fuggoen menne at vagy bukna.
    """
    if weekly is _ALAP:
        weekly = SEMLEGES
    g = FakeGuard(spends)
    g.lasts = dict(lasts or {})
    mod._load_guard = lambda: g
    mod.load = lambda: dict(allapot)
    def ment(d):
        allapot.clear(); allapot.update(d)
    mod.save = ment
    eredeti = mod.read_weekly
    mod.read_weekly = lambda **kw: weekly
    try:
        rc = mod.main(argv)
    finally:
        mod.read_weekly = eredeti
    return rc, g


def main():
    with tempfile.TemporaryDirectory() as td:
        mod.STATE = os.path.join(td, "s.json")
        MIND = {"marveen": 0, "anorak": 0, "cody": 0, "forgacs": 0, "margo": 0, "roxy": 0}

        print("1. A LENYEG: mindenki a SAJAT plafonja alatt, a flotta megis tul a kereten")
        # Egyeni plafonok: fo 300M, subok 120M. Itt senki nem eri el a sajatjat.
        s = dict(MIND, marveen=280_000_000, forgacs=110_000_000, margo=100_000_000,
                 cody=90_000_000)
        allapot = {}
        rc, g = run(s, ["--notify", "--budget", "520000000"], allapot)
        check("exit 0", rc == 0, f"rc={rc}")
        check("van leallitas", bool(g.stopped), f"{g.stopped}")
        check("a fo-agens NEM all le", "marveen" not in g.stopped, f"{g.stopped}")
        check("a harom kolto sub leall", set(g.stopped) == {"forgacs", "margo", "cody"},
              f"{g.stopped}")
        check("a nem kolto ketto NEM all le",
              "anorak" not in g.stopped and "roxy" not in g.stopped, f"{g.stopped}")

        print("2. A kuszob ALATT semmi nem tortenik (negativ kontroll)")
        allapot = {}
        rc, g = run(dict(MIND, marveen=100_000_000), ["--notify", "--budget", "520000000"],
                    allapot)
        check("nem allitott le", not g.stopped, f"{g.stopped}")
        check("nem kuldott", not g.sent, f"{len(g.sent)}")

        print("3. A figyelmeztetesi savban KULD, de NEM allit le")
        allapot = {}
        rc, g = run(dict(MIND, marveen=300_000_000, forgacs=80_000_000),
                    ["--notify", "--budget", "520000000"], allapot)
        check("kuldott", len(g.sent) == 1, f"{len(g.sent)}")
        check("nem allitott le", not g.stopped, f"{g.stopped}")
        check("a savot rogzitette", allapot.get("bands") == [70], f"{allapot}")

        print("4. ugyanaz a sav masodszor: CSEND")
        rc, g = run(dict(MIND, marveen=300_000_000, forgacs=80_000_000),
                    ["--notify", "--budget", "520000000"], allapot)
        check("nem kuldott ujra", not g.sent, f"{len(g.sent)}")

        print("5. a mar leallitott agenst nem allitja le megegyszer")
        allapot = {"day": __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
                   "bands": [85], "stopped": ["forgacs"]}
        rc, g = run(dict(MIND, marveen=280_000_000, forgacs=110_000_000, cody=90_000_000),
                    ["--notify", "--budget", "520000000"], allapot)
        check("forgacs nincs ujra leallitva", "forgacs" not in g.stopped, f"{g.stopped}")
        check("cody viszont igen", "cody" in g.stopped, f"{g.stopped}")

        print("5b. #305: a leallitott, de UJRAINDULT es azota kolto agens ujra leall")
        ma_ = mod.window_day(6)
        allapot = {"day": ma_, "bands": [85], "stopped": ["forgacs"],
                   "stop_activity": {"forgacs": 1000.0}}
        rc, g = run(dict(MIND, marveen=280_000_000, forgacs=110_000_000, cody=90_000_000),
                    ["--notify", "--budget", "520000000"], allapot,
                    lasts={"forgacs": 2000.0, "cody": 1500.0})
        check("forgacs (aktiv a leallitas utan) ujra leall", "forgacs" in g.stopped, f"{g.stopped}")
        check("az uj aktivitas-jel elmentve", allapot.get("stop_activity", {}).get("forgacs") == 2000.0,
              f"{allapot.get('stop_activity')}")

        print("5c. #305: leallitva es azota CSENDES -> nem allitja le megegyszer")
        allapot = {"day": ma_, "bands": [85], "stopped": ["forgacs"],
                   "stop_activity": {"forgacs": 1000.0}}
        rc, g = run(dict(MIND, marveen=280_000_000, forgacs=110_000_000, cody=90_000_000),
                    ["--notify", "--budget", "520000000"], allapot,
                    lasts={"forgacs": 1000.0, "cody": 1500.0})
        check("forgacs nincs ujra leallitva", "forgacs" not in g.stopped, f"{g.stopped}")
        check("cody viszont igen", "cody" in g.stopped, f"{g.stopped}")

        print("6. MERHETETLEN agens: exit 3, nem nyugodt reszosszeg")
        s = {"marveen": 500_000_000}          # a tobbi hianyzik
        allapot = {}
        rc, g = run(s, ["--notify", "--budget", "520000000"], allapot)
        check("exit 3", rc == 3, f"rc={rc}")
        check("nem allitott le", not g.stopped, f"{g.stopped}")

        print("7. --notify NELKUL csak mer")
        allapot = {}
        rc, g = run(dict(MIND, marveen=500_000_000), ["--budget", "520000000"], allapot)
        check("exit 0", rc == 0, f"rc={rc}")
        check("nem allitott le", not g.stopped)
        check("nem kuldott", not g.sent)

        print("8. --no-warn-notify: a FIGYELMEZTETESI savrol nem szol, a LEALLITASROL igen")
        # A gazda kerese, 2026-09-17 08:29 (Telegram msg 2929). Ket ag, mert egy
        # kapcsolo, ami MINDENT elnemit, ugyanugy atmenne egy csak-a-warn tesztet.
        allapot = {}
        rc, g = run(dict(MIND, marveen=300_000_000, forgacs=80_000_000),
                    ["--budget", "520000000", "--notify", "--no-warn-notify"], allapot)
        check("warn savnal nem kuldott", not g.sent, f"{g.sent}")
        check("warn savnal nem allitott le", not g.stopped, f"{g.stopped}")
        allapot = {}
        rc, g = run(dict(MIND, marveen=280_000_000, forgacs=110_000_000, cody=90_000_000),
                    ["--budget", "520000000", "--notify", "--no-warn-notify"], allapot)
        check("stop savnal KULDOTT", len(g.sent) == 1, f"{len(g.sent)}")
        check("stop savnal leallitott", sorted(g.stopped) == ["cody", "forgacs"],
              f"{g.stopped}")

        print("9. Ha a LEALLITAS hire nem megy ki, a kovetkezo futas ujraprobalja")
        class NemaGuard(FakeGuard):
            def notify(self, msg):
                self.sent.append(msg)
                return False
        allapot = {}
        g = NemaGuard(dict(MIND, marveen=280_000_000, forgacs=110_000_000, cody=90_000_000))
        mod._load_guard = lambda: g
        mod.load = lambda: dict(allapot)
        def ment(d):
            allapot.clear(); allapot.update(d)
        mod.save = ment
        _eredeti = mod.read_weekly
        mod.read_weekly = lambda **kw: SEMLEGES
        try:
            mod.main(["--budget", "520000000", "--notify", "--no-warn-notify"])
        finally:
            mod.read_weekly = _eredeti
        check("a leallitas megtortent", sorted(g.stopped) == ["cody", "forgacs"], f"{g.stopped}")
        check("a sav NEM kerult a bands-be", not allapot.get("bands"), f"{allapot.get('bands')}")
        check("fuggoben maradt a hir", sorted(allapot.get("stop_notify_pending") or []) ==
              ["cody", "forgacs"], f"{allapot.get('stop_notify_pending')}")
        # Masodik futas: mar senkit nem kell leallitani, a hirnek megis mennie kell.
        rc, g2 = run(dict(MIND, marveen=280_000_000, forgacs=110_000_000, cody=90_000_000),
                     ["--budget", "520000000", "--notify", "--no-warn-notify"], allapot)
        check("ujrakuldte a hirt", len(g2.sent) == 1, f"{len(g2.sent)}")
        check("nem allitott le ujra", not g2.stopped, f"{g2.stopped}")
        check("a fuggoben lista kiurult", not allapot.get("stop_notify_pending"),
              f"{allapot.get('stop_notify_pending')}")

        # ------------------------------------------------------------------
        # A HETI ABLAK (2026-09-23). A napi szam a MI becslesunk, a heti ablak a
        # valodi korlat. A ket iranyt KULON kell merni: egy teszt, ami csak az
        # override-ot nezi, atmenne egy olyan kapun is, ami mar semmit nem allit le.
        # ------------------------------------------------------------------
        def heti(used, elapsed, window="seven_day"):
            return {"window": window, "used_pct": used, "elapsed_pct": elapsed,
                    "resets_at": 0, "hours_left": 1.0, "age_min": 1.0}

        def fut(spends, argv, allapot, w):
            return run(spends, argv, allapot, weekly=w)

        TULKOLTOK = dict(MIND, marveen=433_000_000, roxy=50_000_000, anorak=20_500_000)

        print("10. OVERRIDE: a napi keret tul van, de a heti ablakban van tartalek")
        # A 2026-09-22 ejszakai valos eset szamai: napi 105%, heti 81%, az ablakbol
        # 99,8% telt el. Anorakot akkor a kapu leallitotta. Ma nem szabad.
        allapot = {}
        rc, g = fut(TULKOLTOK, ["--notify", "--budget", "520000000", "--no-warn-notify"],
                    allapot, heti(81.0, 99.8))
        check("senkit nem allitott le", not g.stopped, f"{g.stopped}")
        check("nem is szolt (no-warn-notify)", not g.sent, f"{g.sent}")

        print("11. KEMENY HETI PLAFON: a nap nyugodt, de a heti ablak a plafon folott")
        allapot = {}
        rc, g = fut(dict(MIND, marveen=100_000_000, roxy=50_000_000),
                    ["--notify", "--budget", "520000000", "--no-warn-notify"],
                    allapot, heti(96.0, 50.0))
        check("a kolto sub leallt", g.stopped == ["roxy"], f"{g.stopped}")
        check("a fo-agens NEM all le", "marveen" not in g.stopped, f"{g.stopped}")
        check("az uzenet a HETI keretet nevezi meg",
              bool(g.sent) and g.sent[0].startswith("CLAUDE HETI KERET"),
              f"{g.sent[:1]}")

        print("12. NEM MERHETO heti szam: NINCS leallitas, egy jelzes naponta (owner msg 7253)")
        # 2026-10-11 elott itt a napi proxy leallitott. A gazda: leallitas elott
        # ellenorizze, hogy tenyleg kell-e. Ha nem tudta ellenorizni, nem allit le.
        allapot = {}
        rc, g = fut(TULKOLTOK, ["--notify", "--budget", "520000000", "--no-warn-notify"],
                    allapot, None)
        check("senkit nem allitott le", not g.stopped, f"{g.stopped}")
        check("szolt rola, es kimondja, hogy nem allitott le",
              len(g.sent) == 1 and "NEM allitottam le" in g.sent[0], f"{g.sent[:1]}")
        rc, g = fut(TULKOLTOK, ["--notify", "--budget", "520000000", "--no-warn-notify"],
                    allapot, None)
        check("masodik futasban mar nem szol", not g.sent and not g.stopped,
              f"{g.sent} {g.stopped}")
        allapot = {}
        rc, g = fut(TULKOLTOK, ["--notify", "--budget", "520000000", "--no-warn-notify",
                                "--ignore-weekly"], allapot, None)
        check("--ignore-weekly mellett a regi viselkedes: leallit",
              sorted(g.stopped) == ["anorak", "roxy"], f"{g.stopped}")

        print("13. NEUTRAL: a heti ablak ELoREFUTOTT, tehat a napi proxy dont")
        # 60% elhasznalva, az ablakbol 45% telt el, a megengedett elorefutas 10 pont:
        # 60 >= 55, tehat nincs override.
        allapot = {}
        rc, g = fut(TULKOLTOK, ["--notify", "--budget", "520000000", "--no-warn-notify"],
                    allapot, heti(60.0, 45.0))
        check("leallitott", sorted(g.stopped) == ["anorak", "roxy"], f"{g.stopped}")
        check("az uzenet a NAPI keretet nevezi meg",
              bool(g.sent) and g.sent[0].startswith("FLOTTA NAPI KERET"), f"{g.sent[:1]}")

        print("14. A hatar a --weekly-lead-en mulik, es kimerheto")
        allapot = {}
        rc, g = fut(TULKOLTOK, ["--notify", "--budget", "520000000", "--no-warn-notify",
                                "--weekly-lead", "20"], allapot, heti(60.0, 45.0))
        check("20 pont elorefutassal mar nem allit le", not g.stopped, f"{g.stopped}")
        allapot = {}
        rc, g = fut(TULKOLTOK, ["--notify", "--budget", "520000000", "--no-warn-notify",
                                "--weekly-lead", "0"], allapot, heti(81.0, 99.8))
        check("nulla elorefutassal a 10. eset MEG MINDIG override", not g.stopped,
              f"{g.stopped}")

        print("15. --ignore-weekly: a 2026-09-23 elotti viselkedes visszakaphato")
        allapot = {}
        rc, g = fut(TULKOLTOK, ["--notify", "--budget", "520000000", "--no-warn-notify",
                                "--ignore-weekly"], allapot, heti(81.0, 99.8))
        check("a heti tartalek ellenere leallitott",
              sorted(g.stopped) == ["anorak", "roxy"], f"{g.stopped}")

        print("16. OVERRIDE mellett a figyelmeztetes KIMONDJA, hogy nem lesz leallitas")
        allapot = {}
        rc, g = fut(TULKOLTOK, ["--notify", "--budget", "520000000"], allapot,
                    heti(81.0, 99.8))
        check("kuldott figyelmeztetest", len(g.sent) == 1, f"{len(g.sent)}")
        check("es abban benne van, hogy NEM allit le senkit",
              bool(g.sent) and "NEM allitok le senkit" in g.sent[0], f"{g.sent[:1]}")
        check("kozben tenyleg nem allitott le", not g.stopped, f"{g.stopped}")

        print("17. read_weekly: mit fogad el meresnek es mit nem")
        import time as _t
        most = _t.time()
        def kep(td, source="authoritative", ok=True, windows=None, gen=None, sl_age=None):
            from datetime import datetime as _dt, timezone as _tz
            g_at = gen if gen is not None else _dt.fromtimestamp(most - 60, _tz.utc).isoformat()
            p = os.path.join(td, "u.json")
            extra = {} if sl_age is None else {"statusline_age_minutes": sl_age}
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"generated_at": g_at,
                           "claude": {"ok": ok, "source": source, **extra,
                                      "windows": windows if windows is not None else
                                      {"seven_day": {"used_percent": 42.0,
                                                     "resets_at": most + 3600}}}}, fh)
            return p

        from datetime import datetime as _dt, timezone as _tz
        p = kep(td)
        w = mod.read_weekly(p, now=most)
        check("friss, hiteles pillanatkep -> meres", w and w["used_pct"] == 42.0, f"{w}")
        check("az eltelt %-ot az ablak vegebol szamolja",
              w and 99.0 < w["elapsed_pct"] < 99.5, f"{w}")

        regi = _dt.fromtimestamp(most - 7200, _tz.utc).isoformat()
        check("ket oras pillanatkep -> NEM meres",
              mod.read_weekly(kep(td, gen=regi), now=most) is None)
        check("becsult (nem hiteles) forras -> NEM meres",
              mod.read_weekly(kep(td, source="estimate"), now=most) is None)
        check("ok=false -> NEM meres", mod.read_weekly(kep(td, ok=False), now=most) is None)
        check("hianyzo ablak -> NEM meres",
              mod.read_weekly(kep(td, windows={}), now=most) is None)
        check("gyorsitotarazott hiteles forras -> meres",
              (mod.read_weekly(kep(td, source="authoritative_cached"), now=most) or {})
              .get("used_pct") == 42.0)
        check("friss allapotsor-forras -> meres (2026-10-11, a 429-es nap)",
              (mod.read_weekly(kep(td, source="statusline", sl_age=0.0), now=most) or {})
              .get("used_pct") == 42.0)
        check("allapotsor-forras kor nelkul -> NEM meres",
              mod.read_weekly(kep(td, source="statusline"), now=most) is None)
        check("friss pillanatkep, de regi allapotsor -> NEM meres (a ket kor osszeadodik)",
              mod.read_weekly(kep(td, source="statusline", sl_age=70.0), now=most) is None)
        ketto = kep(td, windows={"seven_day": {"used_percent": 30.0, "resets_at": most + 3600},
                                 "seven_day_opus": {"used_percent": 77.0,
                                                    "resets_at": most + 3600}})
        w2 = mod.read_weekly(ketto, now=most)
        check("a ket heti ablak kozul a SZUKEBBET valasztja",
              w2 and w2["window"] == "seven_day_opus" and w2["used_pct"] == 77.0, f"{w2}")

        print("17b. az allapotsor-fajl kozvetlen olvasasa: a FRISSEBB meres nyer")
        def slf(td, used, age_min, name="rl.json"):
            q = os.path.join(td, name)
            with open(q, "w", encoding="utf-8") as fh:
                json.dump({"written_at": most - age_min * 60,
                           "rate_limits": {"seven_day": {"used_percentage": used,
                                                         "resets_at": most + 3600}}}, fh)
            return q
        regi_kep = kep(td, gen=_dt.fromtimestamp(most - 40 * 60, _tz.utc).isoformat())
        w3 = mod.read_weekly(regi_kep, now=most, statusline_path=slf(td, 51, 0.5))
        check("friss allapotsor-fajl nyer a 40 perces pillanatkep ellen",
              w3 and w3["used_pct"] == 51.0 and w3["age_min"] == 0.5, f"{w3}")
        w4 = mod.read_weekly(kep(td), now=most, statusline_path=slf(td, 51, 30))
        check("a 30 perces allapotsor-fajl veszit a 1 perces pillanatkep ellen",
              w4 and w4["used_pct"] == 42.0, f"{w4}")
        w5 = mod.read_weekly(kep(td, gen=_dt.fromtimestamp(most - 7200, _tz.utc).isoformat()),
                             now=most, statusline_path=slf(td, 51, 2))
        check("elavult pillanatkep mellett a friss fajl egymaga is meres",
              w5 and w5["used_pct"] == 51.0, f"{w5}")
        check("elavult fajl + elavult pillanatkep -> NEM meres",
              mod.read_weekly(kep(td, gen=_dt.fromtimestamp(most - 7200, _tz.utc).isoformat()),
                              now=most, statusline_path=slf(td, 51, 90)) is None)
        check("hianyzo fajl nem torik el",
              (mod.read_weekly(kep(td), now=most,
                               statusline_path=os.path.join(td, "nincs.json")) or {})
              .get("used_pct") == 42.0)


        print("18. window_day: az allapotfajl nap-kulcsa a 06:00-as horgonyhoz igazodik")
        from datetime import datetime as _dt
        # A hamis hajnali riasztas oka: ejfel es 06:00 kozott a naptari datum mar
        # atfordult, a token-osszeg viszont meg a TEGNAPI napbol jott.
        check("00:00:49 -> meg a TEGNAPI nap kulcsa",
              mod.window_day(6, _dt(2026, 9, 23, 0, 0, 49)) == "2026-09-22",
              mod.window_day(6, _dt(2026, 9, 23, 0, 0, 49)))
        check("05:59 -> meg a tegnapi",
              mod.window_day(6, _dt(2026, 9, 23, 5, 59)) == "2026-09-22")
        check("06:00 -> MAR a mai",
              mod.window_day(6, _dt(2026, 9, 23, 6, 0)) == "2026-09-23")
        check("23:59 -> a mai",
              mod.window_day(6, _dt(2026, 9, 23, 23, 59)) == "2026-09-23")
        check("honap- es evfordulo is atlep",
              mod.window_day(6, _dt(2027, 1, 1, 0, 30)) == "2026-12-31",
              mod.window_day(6, _dt(2027, 1, 1, 0, 30)))
        check("a horgony ORAJA parameter, nem konstans: --daily-from 0 mellett a naptari nap",
              mod.window_day(0, _dt(2026, 9, 23, 0, 0, 49)) == "2026-09-23")


    print()
    if fails:
        print(f"FAIL: {len(fails)} -- {', '.join(fails)}")
        return 1
    print("Minden eset atment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
