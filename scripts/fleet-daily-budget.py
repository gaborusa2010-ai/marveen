#!/usr/bin/env python3
"""
The FLEET's shared daily ceiling -- the half the per-agent caps cannot cover.

Why the per-agent caps are not enough
-------------------------------------
Measured 2026-09-16, and the owner is the one who spotted it: the individual caps
add up to 900M (main 300 + five subs at 120) against a daily budget of 520M. As
long as one or two agents work, nobody reaches their own ceiling and the fleet
stays inside the budget by accident. The moment all six work, every one of them
is "well under the cap" while together they spend 1.7x the day's allowance.

A per-agent gate answers "has THIS agent had enough". It cannot answer "has the
DAY had enough", and that is the question the weekly allowance actually asks.

What it does
------------
Sums every agent's own transcripts since the daily anchor (06:00, the hour the
weekly allowance resets) and compares the total to the fleet budget.

  under the warn band   -> silent
  warn band             -> one Telegram message per band per day
  stop band             -> stops the sub-agents that are ACTUALLY SPENDING,
                           never the main agent

AND SINCE 2026-09-23 THE DAILY NUMBER NO LONGER DECIDES ALONE. The daily budget
is OUR estimate; the real limit is Claude's weekly window, and the gate now reads
it (store/usage-latest.json). Weekly slack lifts the daily stop; a weekly ceiling
stops the fleet even on a quiet day. See the block above read_weekly() for why
both directions are needed and what happens when the weekly number is missing.

WHY IT DOES NOT STOP EVERYONE. An agent that used nothing today did not cause
the overrun, and stopping it buys no tokens back -- it only removes a worker
from tomorrow's queue for no gain. The gate therefore skips anyone below
MIN_SPEND_TO_STOP. That threshold is not politeness: stopping a zero-token agent
is a cost with no measured benefit.

WHY THE MAIN AGENT IS NEVER STOPPED. It is the channel. Stopping it silences the
owner's only way to reach the fleet, and then nobody can even lift the gate.

WHY --exclude-main. The gate never stops the main agent, so counting its spend
only lets it stop the sub-agents in its place: on 2026-10-17 the main agent was
58.7% of the day (375.6M of 640.3M) and Anorak was stopped for it. With the flag
the main agent is still measured and printed, but left out of the total. The
weekly guard is unaffected. Owner decision, Telegram msg 8268 (2026-10-18), on trial.

Usage
-----
  fleet-daily-budget.py                      # measure, print, no action
  fleet-daily-budget.py --notify             # act: warn and stop
  fleet-daily-budget.py --budget 520000000 --stop-at 85 --warn-at 70
  fleet-daily-budget.py --ignore-weekly      # the pre-2026-09-23 behaviour
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "store", "fleet-daily-budget.json")
SUBS = ("anorak", "cody", "forgacs", "margo", "roxy")
MAIN = "marveen"

# Below this an agent is a bystander, not a cause. 20M is roughly 4% of the
# day's budget: enough to be a rounding error, little enough that anyone who
# actually worked is above it (measured 2026-09-16: forgacs 60M, margo 11M).
MIN_SPEND_TO_STOP = 20_000_000

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_spec_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-budget-guard.py")


def _load_guard():
    """Reuse the per-agent guard's measuring and acting code, do not restate it.

    One owner for "how many tokens did agent X spend" and one for "stop agent X".
    A second implementation would drift from the first, and the drift would be
    invisible until a day like the one this file exists for.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent_budget_guard", _spec_src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# A VALODI korlat: a Claude heti ablaka, nem a mi napi token-proxynk
# ---------------------------------------------------------------------------
# 2026-09-23, es a gazda vette eszre (Telegram msg 3633): "Én azt hittem eddig is
# azt nézi, nem csak egy kitalált számot". Igaza volt. Az 520M a MI szamunk; a
# szamlat a Claude heti ablaka allitja ki, es azt ez a kapu eddig nem olvasta.
#
# A MERT ESET, amiert ez a szakasz letezik: 2026-09-22 23:13:21-kor a kapu
# leallitotta Anorakot 13 perc munka utan (20 510 533 token), mikozben a heti
# ablak 81%-on allt es masnap 05:59:59-kor nullazodott. A maradek 19% ugy veszett
# el, hogy volt, aki dolgozott volna belole.
#
# A merce ezert ketto, es a ketto KULONBOZo IRANYBA hat:
#
#   (a) OVERRIDE (engedekenyebb). Ha a heti ablak a sajat linearis utemtervehez
#       kepest tartalekos -- a felhasznalt % nem futott elore az ablakbol ELTELT
#       %-hoz kepest --, akkor a napi proxy tullepese NEM ok a leallitasra.
#
#   (b) KEMENY PLAFON (szigorubb). Ha a heti ablak a sajat kuszobe fole er, a kapu
#       AKKOR IS leallit, ha a napi proxy nyugodt. Ez nem szigoritasi kedv: ha a
#       Claude SAJAT fala allit meg minket, az a fo-agenst is elnemitja, es akkor
#       a gazdanak nincs mivel felengednie a kaput.
#
# A MEGENGEDETT ELoREFUTAS (--weekly-lead) azert nem nulla, mert a leallitas
# gyakorlatilag visszafordithatatlan: a leallitott sub-agens NEM indul ujra
# magatol (lasd a stop-ag uzenetenek kommentjet). Egy szoros linearis merce a nap
# elejen egy fel szazalekpontnyi elorefutasert kilone az ejszakai munkat, es
# reggelig senki nem inditana vissza.
#
# AMIT EZ NEM CSINAL: nem talal ki szamot. Ha a pillanatkep hianyzik, elavult,
# vagy nem hiteles forrasbol jon, a fuggveny None-t ad, es a kapu a regi, napi
# proxyval dolgozik tovabb. EGY NEM MERT HETI SZAM NEM ADHAT FELMENTEST.

USAGE_SNAPSHOT = os.path.join(REPO, "store", "usage-latest.json")
WEEKLY_WINDOW_SEC = 7 * 86400
# A `usage-collect.py` negyedorankent ir. Egy oranal regebbi pillanatkep azt
# jelenti, hogy a gyujto all -- olyankor a heti szam nem meres, hanem emlek.
WEEKLY_MAX_AGE_MIN = 60
_AUTHORITATIVE = ("authoritative", "authoritative_cached", "statusline")
# WHY "statusline" COUNTS (added 2026-10-11, owner msg 7230). When the usage
# endpoint answers 429/403, usage-collect.py falls back to the numbers Claude Code
# itself hands every status-line render -- the same rate_limits.seven_day figure,
# measured identical to the authoritative read on 2026-09-28 (usage-collect.py,
# _read_statusline_rate_limits). Rejecting it here while usage-collect.py already
# alerts on it (ALERTABLE_CLAUDE_SOURCES) made the gate blind on exactly the days
# the endpoint fails: 2026-10-11 14:03 it stopped cody, margo and roxy on the daily
# proxy (89.9%) while the week stood at 50% used / ~62% elapsed -- an override.
# The status-line file has its own age, on top of the snapshot's; both must fit
# inside max_age_min, so a stale render cannot pass as a fresh measurement.


STATUSLINE_RL = os.path.join(REPO, "store", ".claude-rate-limits.json")
# WHY THE GATE READS THE STATUS-LINE FILE ITSELF (owner msg 7253, 2026-10-11:
# "mielott leallitja ... ellenorizze le hogy tenyleg szukseges-e"). The snapshot
# above is rewritten only every 15 minutes, so its status-line figure can already
# be ~15 minutes old when the gate runs. scripts/statusline-ratelimit.sh rewrites
# .claude-rate-limits.json on EVERY status-line render of ANY session, sub-agents
# included -- so whenever someone is spending tokens, this file is minutes old.
# The gate takes whichever of the two readings is FRESHER.


def _read_statusline_file(path, max_age_min, now):
    """Heti ablak kozvetlenul az allapotsor-fajlbol, vagy None."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        written = float(d["written_at"])
        w = ((d.get("rate_limits") or {}).get("seven_day")) or {}
        used, resets = w.get("used_percentage"), w.get("resets_at")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if used is None or not resets:
        return None
    age_min = (now - written) / 60.0
    if age_min > max_age_min or age_min < -5:
        return None
    return _with_pace({"window": "seven_day", "used_pct": float(used),
                       "resets_at": float(resets)}, now, age_min)


def _with_pace(best, now, age_min):
    remaining = best["resets_at"] - now
    elapsed = WEEKLY_WINDOW_SEC - remaining
    best["elapsed_pct"] = max(0.0, min(100.0, elapsed / WEEKLY_WINDOW_SEC * 100.0))
    best["hours_left"] = max(0.0, remaining / 3600.0)
    best["age_min"] = round(age_min, 1)
    return best


def read_weekly(path=None, max_age_min=WEEKLY_MAX_AGE_MIN, now=None, statusline_path=None):
    """A Claude heti ablaka a legfrissebb meresbol, vagy None, ha nem merheto.

    A ket heti ablak (`seven_day` es `seven_day_opus`) kozul a NAGYOBB fogyasztasut
    adja vissza: a szuk keresztmetszet allitja le a flottat, es ma nem mindig
    ugyanaz a ketto kozul.

    Explicit `path` nelkul (eles futas) az allapotsor-fajlt is megnezi, es a
    FRISSEBB meres nyer. Explicit `path` mellett csak azt olvassa, hacsak
    `statusline_path`-t is nem kap (teszt).
    """
    now = now if now is not None else time.time()
    snap = _read_snapshot(path or USAGE_SNAPSHOT, max_age_min, now)
    sl_path = statusline_path or (None if path else STATUSLINE_RL)
    live = _read_statusline_file(sl_path, max_age_min, now) if sl_path else None
    if snap is None or live is None:
        return snap or live
    return live if live["age_min"] < snap["age_min"] else snap


def _read_snapshot(p, max_age_min, now):
    try:
        with open(p, encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, ValueError):
        return None
    cl = snap.get("claude") or {}
    if not cl.get("ok") or cl.get("source") not in _AUTHORITATIVE:
        return None

    gen = snap.get("generated_at")
    try:
        gen_ts = datetime.fromisoformat(str(gen).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None
    age_min = (now - gen_ts) / 60.0
    if cl.get("source") == "statusline":
        try:
            age_min += float(cl.get("statusline_age_minutes"))
        except (TypeError, ValueError):
            return None
    if age_min > max_age_min or age_min < -5:
        return None

    best = None
    for key in ("seven_day", "seven_day_opus"):
        w = (cl.get("windows") or {}).get(key) or {}
        used, resets = w.get("used_percent"), w.get("resets_at")
        if used is None or not resets:
            continue
        if best is None or float(used) > best["used_pct"]:
            best = {"window": key, "used_pct": float(used), "resets_at": float(resets)}
    if best is None:
        return None

    return _with_pace(best, now, age_min)


def weekly_verdict(w, hard_stop, lead, near_reset_min=0.0):
    """stop | resetting | override | neutral | unknown -- mit mond a HETI ablak a napi proxyrol.

    stop      -- a heti ablak onmagaban leallitast indokol (a napi proxy nem szamit)
    resetting -- a plafon folott van, de az ablak `near_reset_min` percen belul
                 nullazodik: a leallitas mar semmit nem nyer (#619)
    override  -- van heti tartalek, a napi proxy tullepese nem ok a leallitasra
    neutral   -- a heti ablak nem szol bele, a napi proxy dont (a regi viselkedes)
    unknown   -- nincs merheto heti szam, a napi proxy dont (a regi viselkedes)

    MIERT A `resetting`. 2026-10-21 05:50:04 a kapu Forgacsot "a nap hatralevo
    reszere" leallitotta 100%-os heti ablaknal, holott a `resets_at` 06:00 volt
    (store/budget-notices.log, Anorak retro 10-21, 1. pont). A leallitas tiz perc
    koltest takaritott meg, cserebe az ujrainditas kezi lett, es a 05:53-as
    napindito "Agent is already running"-gal elbukott. A plafon kozeleben a
    Claude sajat fala ugyis megfogja azt a par percet.
    """
    if not w:
        return "unknown"
    if w["used_pct"] >= hard_stop:
        if near_reset_min > 0 and w.get("hours_left", 999.0) * 60.0 <= near_reset_min:
            return "resetting"
        return "stop"
    if w["used_pct"] < w["elapsed_pct"] + lead:
        return "override"
    return "neutral"


def window_day(daily_from, now=None):
    """Az allapotfajl NAP-kulcsa, UGYANAHHOZ az orahoz horgonyozva, mint a token-ablak.

    MIERT NEM `datetime.now().date()`. A token-osszeget a `window_tokens` a napi
    horgonytol (alapertelmezesben 06:00) szamolja, az allapotfajl `day` kulcsa
    viszont 2026-09-23-ig naptari datum volt. A ketto ejfel es 06:00 kozott
    KULONBOZIK, es ez nem elmeleti: ejfel utan a `day` atfordult, a `bands` es a
    `stopped` halmaz kiurult, a token-osszeg viszont valtozatlanul a TEGNAPI
    napbol jott -- tehat a kapu ujra elkuldte ugyanazt a riasztast, a tegnapi
    szamokkal, es az ejfel utan ket perccel azt sugallta, hogy az UJ nap kerete
    fogyott el.

    Merve ket egymast koveto ejjel, a journalbol: 2026-09-22 00:02:57 es
    2026-09-23 00:00:49 (99,1%). n=2, tehat nem egyszeri.
    """
    now = now or datetime.now()
    if now.hour < daily_from:
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    os.replace(tmp, STATE)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=520_000_000,
                    help="a flotta napi kerete tokenben")
    ap.add_argument("--warn-at", type=int, default=70, help="figyelmeztetesi szazalek")
    ap.add_argument("--stop-at", type=int, default=85, help="leallitasi szazalek")
    ap.add_argument("--daily-from", type=int, default=6, help="a nap kezdo oraja")
    ap.add_argument("--weekly-stop-at", type=float, default=95.0,
                    help="a Claude HETI ablakanak kemeny plafonja szazalekban. "
                         "E folott a kapu akkor is leallit, ha a napi proxy nyugodt: "
                         "a Claude sajat falaba futni rosszabb, mert az a fo-agenst "
                         "is elnemitja.")
    ap.add_argument("--weekly-lead", type=float, default=10.0,
                    help="mennyi elorefutas megengedett szazalekpontban a heti ablak "
                         "linearis utemtervehez kepest, mielott a napi proxy leallithat")
    ap.add_argument("--weekly-near-reset-min", type=float, default=30.0,
                    help="ha a heti ablak ennyi percen belul nullazodik, a heti plafon "
                         "NEM allit le (#619). 0 = a regi viselkedes.")
    ap.add_argument("--weekly-max-age-min", type=float, default=WEEKLY_MAX_AGE_MIN,
                    help="ennel regebbi pillanatkepet nem veszunk meresnek")
    ap.add_argument("--ignore-weekly", action="store_true",
                    help="a 2026-09-23 elotti viselkedes: CSAK a napi token-proxy dont. "
                         "Visszaallashoz es teszthez.")
    ap.add_argument("--notify", action="store_true",
                    help="cselekedjen is: kuldjon es allitson le")
    ap.add_argument("--no-warn-notify", action="store_true",
                    help="a figyelmeztetesi savrol NE szoljon, csak a leallitasrol. "
                         "A gazda kerese, 2026-09-17 08:29 (Telegram msg 2929): "
                         "'a token figyelo csak akkor szoljon nekem, ha valakit le "
                         "kell allitania mara'. Ez KAPCSOLO es nem egy elerhetetlen "
                         "--warn-at szam, mert egy kikapcsolt ertesitesnek latszania "
                         "kell a parancssorban.")
    ap.add_argument("--exclude-main", action="store_true",
                    help="a fo-agens fogyasztasa NE szamitson a napi osszegbe "
                         "(merve es kiirva marad). Gazda, Telegram msg 8268, 2026-10-18.")
    a = ap.parse_args(argv)

    g = _load_guard()
    per_agent, total, missing = {}, 0, []
    last_seen = {}   # name -> newest transcript mtime in the window (activity marker)
    for name in (MAIN,) + SUBS:
        tok, _files, last, miss = g.window_tokens(name, 24, 1.0, a.daily_from)
        if miss:
            missing.append(name)
            continue
        per_agent[name] = tok
        last_seen[name] = last or 0.0
        if not (a.exclude_main and name == MAIN):
            total += tok

    pct = total / a.budget * 100 if a.budget else 0
    heti = None if a.ignore_weekly else read_weekly(max_age_min=a.weekly_max_age_min)
    itelet = weekly_verdict(heti, a.weekly_stop_at, a.weekly_lead, a.weekly_near_reset_min)
    print(json.dumps({"total": total, "budget": a.budget, "pct": round(pct, 1),
                      "per_agent": per_agent, "main_excluded": a.exclude_main,
                      "unmeasurable": missing,
                      "weekly": heti, "weekly_verdict": itelet},
                     ensure_ascii=False))

    if missing:
        # Same rule as the per-agent guard: a gate that cannot measure must not
        # report a comfortable number. Partial data would understate the total.
        sys.stderr.write("fleet-daily-budget: NEM MERHETO: %s\n" % ", ".join(missing))
        return 3

    if not a.notify:
        return 0

    ma = window_day(a.daily_from)
    st = load()
    kuldott = set(st.get("bands") or []) if st.get("day") == ma else set()
    allitott = set(st.get("stopped") or []) if st.get("day") == ma else set()
    # #305 (759434e9): a stopped agent used to be immune for the rest of the day,
    # so one that was restarted after the stop spent freely. Same rule as the
    # sibling agent-budget-guard.py: a stop is "still in force" only while the
    # agent has written NOTHING since it. Activity after the stop re-arms it.
    # A stop recorded before this field existed has no marker: kept as before.
    stop_activity = dict(st.get("stop_activity") or {}) if st.get("day") == ma else {}
    ujra_aktiv = {n for n in allitott
                  if n in stop_activity and last_seen.get(n, 0.0) > stop_activity[n]}
    allitott -= ujra_aktiv
    for n in ujra_aktiv:
        stop_activity.pop(n, None)

    # STOP first: the stop band implies the warn band, and a day that is already
    # over the stop line should not spend a round announcing the smaller number.
    # A leallitas ertesitese az EGYETLEN, ami a gazdahoz eljut (a figyelmeztetesi
    # savot 2026-09-17 ota a --no-warn-notify elnemithatja). Ezert a notify()
    # visszateresi erteket ITT ELLENORIZNI KELL: ha eldobjuk, egy sikertelen
    # kuldes utan az allapotfajl ugy nez ki, mintha szoltunk volna, es az agensek
    # ugy allnak le, hogy arrol SENKI nem tud. Anorak jelezte 2026-09-17 01:0x-kor.
    fuggoben = sorted(st.get("stop_notify_pending") or []) if st.get("day") == ma else []

    # A DONTES, ket forrasbol. A napi proxy egymaga nem eleg (felesleges leallitas,
    # mert nem ismeri a heti tartalekot), es a heti ablak egymaga sem (15 percenkent
    # frissul, tehat egy hirtelen fogyasztast keson vesz eszre).
    #   heti "stop"     -> leallitas akkor is, ha a nap nyugodt
    #   heti "override" -> a napi tullepes NEM ok a leallitasra
    #   egyebkent       -> a napi proxy dont, ahogy 2026-09-23 elott
    #   heti "unknown"  -> NINCS leallitas (owner msg 7253, 2026-10-11): a kapu csak
    #                      akkor allit le, ha leallitas elott ellenorizni TUDTA, hogy
    #                      nincs heti tartalek. Ez nem vakfolt: az allapotsor-fajl
    #                      minden kolto session minden lepesenel frissul, tehat ha
    #                      egy oraja nincs friss szam, akkor egy oraja senki nem
    #                      koltott. Egyszer szol rola naponta. --ignore-weekly
    #                      visszaadja a regi, csak-napi viselkedest.
    vak = itelet == "unknown" and not a.ignore_weekly
    kell_leallitani = (itelet == "stop") or (
        pct >= a.stop_at and itelet != "override" and not vak)
    if vak and pct >= a.stop_at and not fuggoben:
        if st.get("unknown_noted") != ma:
            sz_total = f"{total:,}".replace(",", " ")
            sz_budget = f"{a.budget:,}".replace(",", " ")
            if g.notify(f"FLOTTA NAPI KERET: {pct:.0f}% ({sz_total} / {sz_budget} token).\n\n"
                        "NEM allitottam le senkit: a Claude heti szamat most nem tudtam "
                        "frissen lemerni, es meres nelkul nem allitok le. Errol ma "
                        "tobbet nem szolok."):
                st = dict(st, day=ma, unknown_noted=ma,
                          bands=sorted(kuldott), stopped=sorted(allitott),
                          stop_activity=stop_activity)
                save(st)
        return 0
    if kell_leallitani:
        koltok = sorted(n for n in SUBS
                        if per_agent.get(n, 0) >= MIN_SPEND_TO_STOP and n not in allitott)
        sikeres = [n for n in koltok if g.stop_agent(n)]
        allitott |= set(sikeres)
        for n in sikeres:
            stop_activity[n] = last_seen.get(n, 0.0)
        # Ujraprobalas: akit egy korabbi korben leallitottunk, de a hir nem ment ki.
        hirlista = sorted(set(sikeres) | set(fuggoben))
        if hirlista:
            # AZ EZRES-ELVALASZTO SZOKOZOSITESE A SZAMON TORTENJEN, NE A KESZ UZENETEN.
            # A regi alak egy `.replace(",", " ")`-t futtatott az EGESZ msg-re, tehat a
            # magyar mondatok vesszoit es a felsorolasot is szetverte. Anorak vette eszre
            # 2026-09-19 23:4x, es a hiba REGEBBI, mint az aznapi szoveg-javitas: a
            # kirenderelt uzenetben "cody  forgacs  roxy" allt, es "Aki ma nem koltott  azt
            # nem allitom le" -- vagyis az EREDETI mondatot is rontotta.
            # HOGY MIOTA, AZT NEM MERTEM MEG, es az elso valtozatom itt "evek ota
            # eszrevetlen"-t mondott -- Anorak fogta meg, merve. Amit tudni lehet: e fajl
            # legkorabbi sajat datum-horgonya 2026-09-16, es git-tortenet nincs alatta
            # (2026-09-19-en, a 0349af6 commitban kerult be eloszor). A testverfajlban
            # (agent-budget-guard.py) ugyanez a hiba allt, es ott a legkorabbi horgony
            # 2026-08-31 -- de hogy a vesszo-irtas ABBAN mikor keletkezett, az sincs merve.
            sz_total = f"{total:,}".replace(",", " ")
            sz_budget = f"{a.budget:,}".replace(",", " ")
            # MELYIK korlat sult el. A ket ok kulonbozo valaszt kivan a gazdatol:
            # a napi proxy a MI szamunk (emelheto), a heti ablak a Claude-e (nem).
            if itelet == "stop":
                fejlec = (f"CLAUDE HETI KERET: {heti['used_pct']:.0f}% "
                          f"(a {heti['window']} ablak, plafon {a.weekly_stop_at:.0f}%). "
                          f"A napi keret {pct:.0f}%-on all.")
            else:
                fejlec = f"FLOTTA NAPI KERET: {pct:.0f}% ({sz_total} / {sz_budget} token)."
                if heti:
                    fejlec += (f" A heti ablak {heti['used_pct']:.0f}%, az ablakbol "
                               f"{heti['elapsed_pct']:.0f}% telt el, tehat nincs heti "
                               f"tartalek.")
                else:
                    fejlec += " A heti ablak MOST NEM MERHETO (a napi szam dontott)."
            msg = (f"{fejlec}\n\n"
                   f"Leallitva a nap hatralevo reszere: {', '.join(hirlista)}.\n"
                   # 2026-09-19 ota itt "az INDITAS KEZI" allt, mert akkor semmi nem
                   # inditott ujra leallitott agenst (Anorak merese, harom uton). 2026-10-25
                   # ota van indito: a marveen-<agens>-day-start unit 06:00-kor elinditja,
                   # ha az agent-has-work.py munkat talal (#663, gazda tg 9191), es a
                   # fleet-window-watch.py 06:10-kor riaszt, ha munkaval megis all. A
                   # kapu nem tiltja az inditast, ezt mertuk: Cody 10-24 13:24-es leallitas
                   # utan 10-25 06:00:20-kor elindult. Anorak sajat muszakja 23:00-kor.
                   f"A kovetkezo napi ablak {a.daily_from:02d}:00-kor nyilik, es aki akkor "
                   f"munkaval var, azt a reggeli indito magatol elinditja (Anorakot a "
                   f"23 orai muszakja).\n\n"
                   "A fo-agens NEM all le: o a csatorna. Aki ma nem koltott, "
                   "azt nem allitom le -- nem o okozta.")
            kiment = g.notify(msg)
            if not kiment:
                sys.stderr.write(
                    "fleet-daily-budget: A LEALLITAS MEGTORTENT, DE A HIR NEM MENT KI "
                    "(%s). A kovetkezo futas ujraprobalja.\n" % ", ".join(hirlista))
            st = {"day": ma,
                  "bands": sorted(kuldott | {a.stop_at}) if kiment else sorted(kuldott),
                  "stopped": sorted(allitott),
                  "stop_activity": stop_activity,
                  "stop_notify_pending": [] if kiment else hirlista}
            save(st)
        return 0

    if a.no_warn_notify:
        return 0

    if pct >= a.warn_at and a.warn_at not in kuldott:
        reszletek = "\n".join(f"  {n}: {per_agent.get(n,0)/1e6:.0f}M"
                              for n in sorted(per_agent, key=lambda x: -per_agent[x])
                              if per_agent.get(n, 0) > 0)
        # Ugyanaz a javitas, mint a stop-agban: a szokozosites a SZAMON fut, nem a
        # kesz uzeneten (lasd az ottani kommentet, Anorak lelete 2026-09-19).
        sz_total = f"{total:,}".replace(",", " ")
        sz_budget = f"{a.budget:,}".replace(",", " ")
        if itelet == "override":
            # A napi proxy mar a stop-sav folott jarhat, es MEGSEM allitunk le.
            # Ha ezt nem mondjuk ki, a gazda egy "85%-nal leallnak" mondatot olvas,
            # aztan reggel azt latja, hogy senki nem allt le -- es a kapuban fog
            # ketelkedni, nem a mondatban.
            zaras = (f"NEM allitok le senkit: a Claude heti ablaka {heti['used_pct']:.0f}%, "
                     f"az ablakbol {heti['elapsed_pct']:.0f}% telt el, tehat van heti "
                     f"tartalek. A napi keret a mi sajat becslesunk, a heti ablak a "
                     f"valodi korlat. Errol a savrol ma tobbet nem szolok.")
        else:
            zaras = (f"{a.stop_at}%-nal a ma kolto sub-agensek leallnak a nap hatralevo "
                     "reszere. Errol a savrol ma tobbet nem szolok.")
        msg = (f"FLOTTA NAPI KERET: {pct:.0f}% ({sz_total} / {sz_budget} token).\n\n"
               f"{reszletek}\n\n"
               f"{zaras}")
        if g.notify(msg):
            st = {"day": ma, "bands": sorted(kuldott | {a.warn_at}),
                  "stopped": sorted(allitott), "stop_activity": stop_activity}
            save(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
