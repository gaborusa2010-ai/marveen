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

WHY IT DOES NOT STOP EVERYONE. An agent that used nothing today did not cause
the overrun, and stopping it buys no tokens back -- it only removes a worker
from tomorrow's queue for no gain. The gate therefore skips anyone below
MIN_SPEND_TO_STOP. That threshold is not politeness: stopping a zero-token agent
is a cost with no measured benefit.

WHY THE MAIN AGENT IS NEVER STOPPED. It is the channel. Stopping it silences the
owner's only way to reach the fleet, and then nobody can even lift the gate.

Usage
-----
  fleet-daily-budget.py                      # measure, print, no action
  fleet-daily-budget.py --notify             # act: warn and stop
  fleet-daily-budget.py --budget 520000000 --stop-at 85 --warn-at 70
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
    ap.add_argument("--notify", action="store_true",
                    help="cselekedjen is: kuldjon es allitson le")
    ap.add_argument("--no-warn-notify", action="store_true",
                    help="a figyelmeztetesi savrol NE szoljon, csak a leallitasrol. "
                         "A gazda kerese, 2026-09-17 08:29 (Telegram msg 2929): "
                         "'a token figyelo csak akkor szoljon nekem, ha valakit le "
                         "kell allitania mara'. Ez KAPCSOLO es nem egy elerhetetlen "
                         "--warn-at szam, mert egy kikapcsolt ertesitesnek latszania "
                         "kell a parancssorban.")
    a = ap.parse_args(argv)

    g = _load_guard()
    per_agent, total, missing = {}, 0, []
    for name in (MAIN,) + SUBS:
        tok, _files, _last, miss = g.window_tokens(name, 24, 1.0, a.daily_from)
        if miss:
            missing.append(name)
            continue
        per_agent[name] = tok
        total += tok

    pct = total / a.budget * 100 if a.budget else 0
    print(json.dumps({"total": total, "budget": a.budget, "pct": round(pct, 1),
                      "per_agent": per_agent, "unmeasurable": missing},
                     ensure_ascii=False))

    if missing:
        # Same rule as the per-agent guard: a gate that cannot measure must not
        # report a comfortable number. Partial data would understate the total.
        sys.stderr.write("fleet-daily-budget: NEM MERHETO: %s\n" % ", ".join(missing))
        return 3

    if not a.notify:
        return 0

    ma = datetime.now().strftime("%Y-%m-%d")
    st = load()
    kuldott = set(st.get("bands") or []) if st.get("day") == ma else set()
    allitott = set(st.get("stopped") or []) if st.get("day") == ma else set()

    # STOP first: the stop band implies the warn band, and a day that is already
    # over the stop line should not spend a round announcing the smaller number.
    # A leallitas ertesitese az EGYETLEN, ami a gazdahoz eljut (a figyelmeztetesi
    # savot 2026-09-17 ota a --no-warn-notify elnemithatja). Ezert a notify()
    # visszateresi erteket ITT ELLENORIZNI KELL: ha eldobjuk, egy sikertelen
    # kuldes utan az allapotfajl ugy nez ki, mintha szoltunk volna, es az agensek
    # ugy allnak le, hogy arrol SENKI nem tud. Anorak jelezte 2026-09-17 01:0x-kor.
    fuggoben = sorted(st.get("stop_notify_pending") or []) if st.get("day") == ma else []
    if pct >= a.stop_at:
        koltok = sorted(n for n in SUBS
                        if per_agent.get(n, 0) >= MIN_SPEND_TO_STOP and n not in allitott)
        sikeres = [n for n in koltok if g.stop_agent(n)]
        allitott |= set(sikeres)
        # Ujraprobalas: akit egy korabbi korben leallitottunk, de a hir nem ment ki.
        hirlista = sorted(set(sikeres) | set(fuggoben))
        if hirlista:
            msg = (f"FLOTTA NAPI KERET: {pct:.0f}% ({total:,} / {a.budget:,} token).\n\n"
                   f"Leallitva a nap hatralevo reszere: {', '.join(hirlista)}.\n"
                   f"Holnap 06:00-kor tiszta lappal indulnak.\n\n"
                   "A fo-agens NEM all le: o a csatorna. Aki ma nem koltott, "
                   "azt nem allitom le -- nem o okozta.").replace(",", " ")
            kiment = g.notify(msg)
            if not kiment:
                sys.stderr.write(
                    "fleet-daily-budget: A LEALLITAS MEGTORTENT, DE A HIR NEM MENT KI "
                    "(%s). A kovetkezo futas ujraprobalja.\n" % ", ".join(hirlista))
            st = {"day": ma,
                  "bands": sorted(kuldott | {a.stop_at}) if kiment else sorted(kuldott),
                  "stopped": sorted(allitott),
                  "stop_notify_pending": [] if kiment else hirlista}
            save(st)
        return 0

    if a.no_warn_notify:
        return 0

    if pct >= a.warn_at and a.warn_at not in kuldott:
        reszletek = "\n".join(f"  {n}: {per_agent.get(n,0)/1e6:.0f}M"
                              for n in sorted(per_agent, key=lambda x: -per_agent[x])
                              if per_agent.get(n, 0) > 0)
        msg = (f"FLOTTA NAPI KERET: {pct:.0f}% ({total:,} / {a.budget:,} token).\n\n"
               f"{reszletek}\n\n"
               f"{a.stop_at}%-nal a ma kolto sub-agensek leallnak a nap hatralevo "
               "reszere. Errol a savrol ma tobbet nem szolok.").replace(",", " ")
        if g.notify(msg):
            st = {"day": ma, "bands": sorted(kuldott | {a.warn_at}),
                  "stopped": sorted(allitott)}
            save(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
