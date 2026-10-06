#!/usr/bin/env python3
"""Running but NOT working: the agent that is alive all day and does nothing.

Why this exists
---------------
Measured 2026-09-17 16:4x, and the OWNER is the one who spotted it, on Telegram
(msg 2941): "Hogyhogy Cody es Roxy nem kaptak munkat??? Hetvegen meg lett
beszelve, hogy mit fognak csinalni..."

Both agents had been running since 05:23 (autoRestart, mode=continue) and by
16:40 had spent ZERO tokens, sent ZERO messages and received ZERO messages --
thirteen hours alive and idle. Work was not missing: cody had 7 open cards,
roxy 3, plus their own sections in the weekend plan. What was missing was the
HANDOVER. A running agent does not start on its own cards; it sleeps until
someone writes to it, and neither had a scheduled task to wake them.

Nothing caught it, and that is the point:
  - the budget guards only look at spending too MUCH; zero is invisible to them;
  - the kanban audit watches in_progress cards -- these were planned/waiting;
  - the daily digest groups by chat_id, not by agent;
  - /api/agents says running=true, and that LOOKS reassuring.
None of those meters can answer "is this agent WORKING", so none of them can
say no.

What it does NOT do
-------------------
It never starts, stops or messages an agent. It reports. The owner's own
constraint, same conversation (msg 2944): "csak olyan agentnek akinek van
munkaja aznapra... nem kell mindenkit egyszerre elinditani...akkor megint hamar
elfogynak a tokenek". So the signal needs all THREE conditions together --
running, idle, AND has work -- and an agent with nothing to do stays off the
report entirely.

  idle-agent-guard.py                 # measure, print JSON, no action
  idle-agent-guard.py --notify        # also send one Telegram line
  idle-agent-guard.py --max-idle-tokens 1000000 --daily-from 6
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(REPO, "store")
STATE = os.path.join(STORE, "idle-agent-guard.json")
TOKEN_FILE = os.path.join(STORE, ".dashboard-token")
SUBS = ("anorak", "cody", "forgacs", "margo", "roxy")
API = "http://localhost:%s" % (os.environ.get("MARVEEN_WEB_PORT") or "3420")

# NOT FROM MEASUREMENT, and saying so is the point (the fleet's own rule: a
# number that looks measured but is not is worse than no number). The reasoning:
# window_tokens counts cache_read_input_tokens at weight 1.0, and a single turn
# on a loaded context is already millions -- today's measured day was marveen
# 119.5M and forgacs 42.1M, and Anorak reached 18.6M by 23:18 last night. So 1M
# is roughly 2% of even a light day: an agent below it did not work, it idled.
# TO MEASURE IT PROPERLY: collect the smallest non-zero daily per-agent total
# over a few weeks and put the floor under that. Until then this is a judgement
# call with a stated basis, not a measurement.
DEFAULT_MAX_IDLE = 1_000_000


def _load_guard():
    """Reuse the per-agent guard's measuring code, do not restate it.

    Same reason fleet-daily-budget.py does it: one owner for "how many tokens
    did agent X spend". A second implementation drifts, and the drift is
    invisible until a day like the one this file exists for.
    """
    import importlib.util
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-budget-guard.py")
    spec = importlib.util.spec_from_file_location("agent_budget_guard", src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def api_get(path):
    """GET the dashboard API. Raises on anything that is not a 200 + JSON.

    It RAISES rather than returning a default, because a defensive
    `.get(a, .get(b, []))` chain turns an error body into "nothing found" --
    measured 2026-09-17 on GET /api/kanban?limit=200, which answers 400 while
    the chain read it as zero cards out of 136. A meter that cannot fail
    cannot say no.
    """
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        tok = fh.read().strip()
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=15) as r:
        if r.status != 200:
            raise RuntimeError("%s -> HTTP %s" % (path, r.status))
        return json.loads(r.read().decode("utf-8"))


def running_agents():
    """{name: bool}. The field is `name`, NOT `id` -- asking for `id` returns
    None for all five and silently drops everyone from the filter."""
    rows = api_get("/api/agents")
    if not isinstance(rows, list):
        raise RuntimeError("/api/agents: nem lista, hanem %s" % type(rows).__name__)
    out = {}
    for r in rows:
        if "name" not in r:
            raise RuntimeError("/api/agents: nincs `name` mezo, kulcsok: %s" % sorted(r))
        out[r["name"]] = bool(r.get("running"))
    return out


def open_work():
    """{name: count of open, non-archived cards}. GET /api/kanban accepts NO
    query parameters -- not even ?limit=, it answers 400."""
    rows = api_get("/api/kanban")
    if not isinstance(rows, list):
        raise RuntimeError("/api/kanban: nem lista, hanem %s" % type(rows).__name__)
    out = {n: 0 for n in SUBS}
    for c in rows:
        who = (c.get("assignee") or "").strip().lower()
        if who in out and c.get("status") != "done" and not c.get("archived_at"):
            out[who] += 1
    return out


def should_alert(running, tokens, work_count, max_idle_tokens):
    """The whole decision, pure, so a test can drive BOTH answers.

    All three conditions together, per the owner's constraint: an agent with no
    work for the day is not a finding, it is a rest day.
    """
    return bool(running) and tokens <= max_idle_tokens and work_count > 0


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
    ap.add_argument("--max-idle-tokens", type=int, default=DEFAULT_MAX_IDLE,
                    help="ennel kevesebb token = nem dolgozott (NEM meresbol, lasd a fejlecet)")
    ap.add_argument("--daily-from", type=int, default=6, help="a nap kezdo oraja")
    ap.add_argument("--notify", action="store_true", help="kuldjon Telegram-sort is")
    a = ap.parse_args(argv)

    g = _load_guard()
    run = running_agents()
    work = open_work()

    rows, unmeasurable = [], []
    for name in SUBS:
        tok, _files, _last, miss = g.window_tokens(name, 24, 1.0, a.daily_from)
        if miss:
            unmeasurable.append(name)
            continue
        rows.append({"agent": name, "running": run.get(name, False), "tokens": tok,
                     "open_cards": work.get(name, 0),
                     "alert": should_alert(run.get(name, False), tok,
                                           work.get(name, 0), a.max_idle_tokens)})

    hits = [r for r in rows if r["alert"]]
    print(json.dumps({"rows": rows, "alert_count": len(hits),
                      "unmeasurable": unmeasurable}, ensure_ascii=False))

    if unmeasurable:
        # A missing transcript dir is not a quiet agent. Never let it read as one.
        sys.stderr.write("idle-agent-guard: NEM MERHETO: %s\n" % ", ".join(unmeasurable))

    if not a.notify or not hits:
        return 0

    today = time.strftime("%Y-%m-%d")
    st = load()
    done = st.get(today) or []
    fresh = [r for r in hits if r["agent"] not in done]
    if not fresh:
        return 0

    msg = ("FUT, DE NEM DOLGOZIK: %s.\n\n" % ", ".join(r["agent"] for r in fresh)
           + "\n".join("  %s: %.1fM token ma, %d nyitott kartya"
                       % (r["agent"], r["tokens"] / 1e6, r["open_cards"]) for r in fresh)
           + "\n\nMunkaja van, de nem kapott atadast. Nem inditottam es nem "
             "allitottam le senkit.")
    # Check the send. The stop branch of fleet-daily-budget.py dropped notify()'s
    # return value until 2026-09-17, so a failed send marked itself as delivered
    # and never retried. Same mistake, one file later, would be a choice.
    if g.notify(msg, to_owner=True):
        st[today] = done + [r["agent"] for r in fresh]
        save(st)
    else:
        sys.stderr.write("idle-agent-guard: az ertesites NEM ment ki, a kovetkezo "
                         "futas ujraprobalja\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
