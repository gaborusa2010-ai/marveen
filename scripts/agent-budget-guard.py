#!/usr/bin/env python3
"""
Hard token ceiling for ONE agent, enforced without a model turn.

Why this exists
---------------
Anorak (the researcher) runs for hours at night with nobody watching. Every
other agent is bounded by a human being present: the owner notices, and stops
it. That check does not exist at 3am, and the failure is not a bad report -- it
is the owner's whole daily quota gone before he wakes up.

The fleet had no per-agent spend ceiling. `usage-watch.py` measures the WHOLE
install against a ceiling it has not yet learned, and `context-guard` restarts
an agent on context saturation, which is about the conversation, not the cost.
Neither can say "this one agent has had enough".

What it enforces
----------------
Tokens attributable to the agent's OWN transcripts inside a rolling window.
Over the cap -> stop the agent through the dashboard API and send one Telegram
message. Not a warning it can ignore: the process is stopped.

Why its own transcripts and not the global number: the global figure is the
whole fleet, so a busy day elsewhere would stop an innocent agent, and a quiet
day elsewhere would let a runaway keep running. The question here is what THIS
agent spent.

Why the numbers are honest
--------------------------
It counts input, output and both cache fields, because the plan meters them
all. It reports what it measured, not a percentage of a ceiling nobody has
calibrated -- inventing a percentage is the trap usage-watch.py exists to avoid.

Repeat protection
-----------------
Once it stops an agent it records that, and will not stop or announce it again
until the agent is running afresh (transcript activity after the stop). A guard
that shouts every ten minutes gets turned off, and then it guards nothing.

Usage
-----
  agent-budget-guard.py --agent anorak --cap 40000000            # check, act
  agent-budget-guard.py --agent anorak --cap 40000000 --dry-run  # measure only
  agent-budget-guard.py --agent anorak --cap 40000000 --window 5

Exit 0 always unless something actually broke (2).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(REPO, "store")
STATE = os.path.join(STORE, "agent-budget-guard.json")
# #417 (626f3223): a same-day exemption. A restart after a stop USED to be
# re-stopped on the next tick (fresh transcript activity re-arms the guard),
# while the stop message promised "restart it on the dashboard". The only
# ways a restart can hold are a higher cap or this file: {"agent": "YYYY-MM-DD"}
# -> on that local day the guard does not stop the agent (it still logs).
OVERRIDE = os.path.join(STORE, "agent-budget-override.json")


def override_today(agent, daily_from=None):
    """True if the agent is exempted for the day being counted.

    With a --daily-from window the counted day runs from <hour>:00 to <hour>:00,
    so between midnight and that hour it is still YESTERDAY's day. Comparing the
    exemption with the calendar date let it expire at midnight while the window
    kept counting (Anorak retro 2026-10-14: margo stopped at 00:06 despite
    {"margo": "2026-10-13"}). The exemption therefore names the counted day.
    """
    try:
        with open(OVERRIDE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    if daily_from is not None:
        day = datetime.fromtimestamp(day_start(daily_from)).strftime("%Y-%m-%d")
    else:
        day = datetime.now().strftime("%Y-%m-%d")
    return isinstance(data, dict) and data.get(agent) == day
PROJECTS = os.path.expanduser("~/.claude/projects")


def project_slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _tg_env():
    """Bot token file: install-scoped first (#915), legacy HOME as fallback.

    Measured 2026-09-16: ~/.claude/channels was empty while the real token sat
    in <install>/.claude/channels/telegram/.env, so this module could not send
    anything. The shell senders (scripts/limit-monitor.sh) already tried both.
    """
    local = os.path.join(REPO, ".claude", "channels", "telegram", ".env")
    if os.path.exists(local):
        return local
    return os.path.expanduser("~/.claude/channels/telegram/.env")


TG_ENV = _tg_env()
CHAT_ID = "8547011582"

TOKEN_FIELDS = ("input_tokens", "output_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens")


# The main agent is not under agents/: its cwd IS the install root, so its
# project dir has no "-agents-<name>" tail. Without this the guard reports
# "NEM MERHETO" for it forever -- correctly, since the directory really is
# absent, but for the wrong reason: the path was built from the wrong rule.
MAIN_AGENT_DIRNAME = project_slug(REPO)


# The main agent runs under its own CLAUDE_CONFIG_DIR, so its transcripts are NOT
# under ~/.claude/projects. Every other agent is, which is why this went unnoticed.
MAIN_AGENT_PROJECTS = os.path.join(REPO, ".channels-config", "projects")


def project_dir(agent):
    """Claude Code names a project dir after its cwd, with / replaced by -.

    BLINDSPOT901 (Anorak, 2026-09-20; Marveen verified independently with this very
    function). The main agent's config root is CLAUDE_CONFIG_DIR=<install>/.channels-config,
    so Claude Code writes its transcripts under THAT root, not under ~/.claude. The old
    path still EXISTS -- it holds sessions from before the switch -- so nothing looked
    broken. It simply had no file newer than today's cutoff.

    WHAT THAT COST, measured the day it was found: the fleet gate reported 413M tokens
    (79.5% of the daily budget) while the real total was 610M (117.3%). The 85% stop band
    was never crossed on paper, so the gate stopped nobody all morning -- and the owner
    enabled extra credit on the strength of the wrong number.

    WHY SILENTZERO901 DID NOT CATCH IT, and this is the part worth keeping: that guard
    returns missing=True when the project dir is ABSENT. This directory was present and
    EMPTY-for-today. `os.path.isdir` passed, every old file fell out on `mt < cutoff`, and
    window_tokens returned a CONFIDENT zero with missing=False. The protection covers the
    missing directory, not the emptied one -- and Forgacs' own comment on it demands the
    caller be able to tell "nothing used" from "nothing measured". At the single biggest
    consumer, it could not.

    A fallback rather than a replacement: if the new path has no directory, the old one is
    still returned, so a rollback of the config root does not blind this the other way.
    """
    if agent == "marveen":
        primary = os.path.join(MAIN_AGENT_PROJECTS, MAIN_AGENT_DIRNAME)
        if os.path.isdir(primary):
            return primary
        return os.path.join(PROJECTS, MAIN_AGENT_DIRNAME)
    slug = project_slug(os.path.join(REPO, "agents", agent))
    shared = os.path.join(PROJECTS, slug)
    if os.path.isdir(shared):
        return shared
    # The newer agents (prospero, merlin, ariel, ...) have their own config root whose
    # projects/ is NOT a symlink to ~/.claude/projects, so they never appear there.
    # Measured 2026-10-26: prospero's 09:34-10:00 extra-credit spend read as null.
    own = os.path.join(REPO, "agents", agent, ".claude-config", "projects", slug)
    if os.path.isdir(own):
        return own
    return shared


def day_start(reset_hour):
    """Epoch of the most recent <reset_hour>:00 local time.

    A DAILY cap is not a 24-hour rolling one, and the difference is the whole
    point of the owner's instruction on 2026-09-16: "alljon meg arra a napra, es
    holnap folytatja". Rolling, an agent stopped at 20:00 stays stopped until
    20:00 tomorrow -- it never gets a fresh morning. Anchored, the window clears
    at a fixed hour and tomorrow starts empty.

    The anchor is 06:00 by default because that is when the account's weekly
    allowance resets (measured 2026-09-16: reset 09-23 05:59 local), so the daily
    slices line up with the week they are carved out of instead of straddling it.
    """
    now = datetime.now()
    start = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    if start > now:
        start = start - timedelta(days=1)
    return start.timestamp()


def window_tokens(agent, hours, cache_read_weight=1.0, daily_from=None):
    """Tokens in the agent's own transcripts within the window.

    `daily_from` (an hour, 0-23) switches from a rolling `hours` window to a
    calendar-day one anchored at that hour. Returns (total, files,
    last_activity_epoch, missing). last_activity matters for the repeat guard:
    it is how we tell a fresh run from the one we already stopped.
    """
    d = project_dir(agent)
    cutoff = day_start(daily_from) if daily_from is not None else time.time() - hours * 3600
    total = 0
    files = 0
    last = 0.0
    # Claude Code writes one JSONL line per content block of an API response,
    # and EVERY such line carries the full usage of that response (same
    # message.id / requestId). Summing lines counted each response 1.9-2.1x
    # (Anorak retro 2026-10-14, measured on margo and cody). Count each once.
    seen = set()
    if not os.path.isdir(d):
        # SILENTZERO901 (Forgacs, 2026-09-01): a missing project dir used to
        # return the same shape as a quiet agent -- 0 tokens, 0 files, exit 0,
        # "1.3%"-style output replaced by a serene 0.0%. A typo in the agent
        # name, a rename, or a changed transcript path therefore produced a
        # guard that reports "well under the cap" forever and never intervenes,
        # while looking exactly like a working guard. The caller must be able to
        # tell "nothing used" from "nothing measured".
        return 0, 0, 0.0, True
    for root, _dirs, names in os.walk(d):
        for n in names:
            if not n.endswith(".jsonl"):
                continue
            p = os.path.join(root, n)
            try:
                mt = os.path.getmtime(p)
                last = max(last, mt)
                if mt < cutoff:
                    continue
                files += 1
                with open(p, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if '"usage"' not in line:
                            continue
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        ts = rec.get("timestamp")
                        if ts:
                            try:
                                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                if t.timestamp() < cutoff:
                                    continue
                            except ValueError:
                                pass
                        msg = rec.get("message") or {}
                        key = msg.get("id") or rec.get("requestId")
                        if key:
                            if key in seen:
                                continue
                            seen.add(key)
                        u = (msg.get("usage") or rec.get("usage") or {})
                        for k in TOKEN_FIELDS:
                            v = int(u.get(k) or 0)
                            # A gazda dontese 2026-09-05: a plafon az 5 oras
                            # ablakhoz kotodjon. A MERES ugyanaznap: a szukites
                            # 8 -> 5 ora ONMAGABAN semmit nem valtoztat, mert a
                            # 61 490 460 tokenbol 60 738 335 EGYETLEN oraban
                            # keletkezett -- mindket ablak ugyanazt a szamot
                            # adja. Ami tenylegesen kitolti a plafont, az a
                            # cache-olvasas: 97,7%. Ezert kap sulyt.
                            if k == "cache_read_input_tokens":
                                v = int(v * cache_read_weight)
                            total += v
            except OSError:
                continue
    return total, files, last, False


def load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d):
    os.makedirs(STORE, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def stop_agent(agent):
    """Stop through the dashboard API. True only if the API said so.

    Checks the ANSWER, not curl's exit code: curl returns 0 on a 401 too, and a
    guard that believes it stopped something it did not is worse than no guard.
    """
    try:
        token = open(os.path.join(STORE, ".dashboard-token"), encoding="utf-8").read().strip()
    except OSError as e:
        print(f"HIBA: nincs dashboard token ({e})", file=sys.stderr)
        return False
    port = os.environ.get("MARVEEN_WEB_PORT", "3420")
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "20", "-w", "\n%{http_code}", "-X", "POST",
             f"http://localhost:{port}/api/agents/{agent}/stop",
             "-H", f"Authorization: Bearer {token}"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"HIBA: a stop hivas nem futott: {e}", file=sys.stderr)
        return False
    body = r.stdout or ""
    code = body.strip().rsplit("\n", 1)[-1]
    if code in ("200", "201"):
        return True
    # ALREADY STOPPED IS SUCCESS. The API answers 400 {"error":"Agent is not
    # running"} for an agent that is not running, and the goal here is a state,
    # not an action: if it is not running, the ceiling is enforced. Measured on
    # 2026-08-31 against a configured-but-never-started agent.
    # Treating this as failure would send "THE STOP FAILED" and, because a
    # failure is deliberately not committed, repeat it every ten minutes -- the
    # exact noise that gets a guard switched off, after which it guards nothing.
    if code == "400" and "not running" in body.lower():
        print("# a stop 400-at adott: az agens amugy sem fut, tehat kesz", file=sys.stderr)
        return True
    print(f"HIBA: a stop elutasitva, http={code}: {body[:200]}", file=sys.stderr)
    return False


def notify(text, to_owner=False):
    # Routine budget notices no longer reach Telegram (owner, msg 5764,
    # 2026-10-04). Only an explicit to_owner=True goes out: a FAILED stop, and
    # fleet-window-watch.py's release notice, which the owner chose (msg 3774).
    if not to_owner:
        return log_only(text)
    try:
        env = open(TG_ENV, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print(f"HIBA: a Telegram token nem olvashato ({e})", file=sys.stderr)
        return False
    m = re.search(r"[0-9]+:[A-Za-z0-9_-]+", env)
    if not m:
        print("HIBA: nincs bot token a channels .env-ben", file=sys.stderr)
        return False
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "15",
             f"https://api.telegram.org/bot{m.group()}/sendMessage",
             "--data-urlencode", f"chat_id={CHAT_ID}",
             "--data-urlencode", f"text={text}",
             "--data-urlencode", "disable_web_page_preview=true"],
            capture_output=True, text=True, timeout=25)
        return json.loads(r.stdout or "{}").get("ok") is True
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        print(f"HIBA: az ertesites nem ment ki: {e}", file=sys.stderr)
        return False


NOTICE_LOG = os.path.join(REPO, "store", "budget-notices.log")


def log_only(text):
    """Record a routine budget notice WITHOUT sending it to Telegram.

    The owner, Telegram msg 5764, 2026-10-04: "Ezeket az uzeneteket vedd mar
    ki, nem akarom latni oket" (in reply to a routine stop notice). The stop
    and warn notices now land here and in the journal; a FAILED stop still goes
    out through notify(to_owner=True), because that one needs a hand on the
    dashboard.
    Returns True so callers record the notice as delivered and do not retry.
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"# notice (nem ment Telegramra): {text}", file=sys.stderr)
    try:
        with open(NOTICE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {text}\n\n")
    except OSError as e:
        print(f"HIBA: a notice-naplo nem irhato ({e})", file=sys.stderr)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--cap", type=int, required=True, help="token plafon az ablakban")
    ap.add_argument("--window", type=float, default=5.0, help="gordulo ablak oraban")
    ap.add_argument("--cache-read-weight", type=float, default=1.0,
                    help="a cache_read_input_tokens sulya a plafonban (1.0 = teljes)")
    ap.add_argument("--margin", type=int, default=0,
                    help="biztonsagi savszelesseg tokenben: a leallitas mar "
                         "(cap - margin)-nal megtortenik, hogy az agens NE erje el a plafont")
    ap.add_argument("--dry-run", action="store_true", help="csak mer, nem allit le")
    ap.add_argument("--daily-from", type=int, default=None, metavar="ORA",
                    help="naptari napi ablak: a cutoff a legutobbi ORA:00 (pl. 6), "
                         "nem gordulo --window")
    ap.add_argument("--warn-only", action="store_true",
                    help="a kuszobnel NE allitsa le, csak kuldjon egy figyelmeztetest "
                         "(a fo-agensre valo: leallitani annyi, mint a csatornat elnemitani)")
    a = ap.parse_args(argv)

    total, files, last, missing = window_tokens(a.agent, a.window, a.cache_read_weight,
                                                a.daily_from)
    # A gazda kerese 2026-09-05 10:42: "alljon le meg az elott hogy elerne a
    # hatart". Ezert nem a plafonnal hasonlitunk, hanem egy alatta levo
    # kuszobbel. A margin MERT szam, nem tipp: Anorak eddigi legrosszabb
    # 10 PERCES sulyozott loketé 2 493 747 token volt (2026-09-04 23:50),
    # a legrosszabb 3 PERCES 1 127 462. A timer 3 percenkent fut, tehat egy
    # 5 000 000-es sav a mert csucs negyszerese felett all.
    trigger = max(0, a.cap - a.margin)
    pct = (total / a.cap * 100) if a.cap else 0
    ablak = f"{a.daily_from:02d}:00 ota" if a.daily_from is not None else f"{a.window:g} ora alatt"
    print(json.dumps({"agent": a.agent, "window_h": a.window,
                      "daily_from": a.daily_from, "warn_only": a.warn_only, "tokens": total,
                      "cap": a.cap, "margin": a.margin, "trigger": trigger,
                      "pct": round(pct, 1), "files": files,
                      "project_dir_missing": missing},
                     ensure_ascii=False))

    if missing:
        # Loud on purpose, and non-zero so systemd marks the unit failed instead
        # of logging a reassuring 0.0% every ten minutes. A guard that cannot
        # measure must not report a comfortable number.
        sys.stderr.write(
            "agent-budget-guard: NEM MERHETO -- nincs ilyen projekt-mappa: %s\n"
            "A '%s' agens neve elgepelt, at lett nevezve, vagy a transcript-utvonal "
            "valtozott. A plafon-orzes ezzel az agenssel NEM MUKODIK.\n"
            % (project_dir(a.agent), a.agent))
        return 3

    if a.dry_run:
        return 0

    when = datetime.now().strftime("%H:%M")
    if a.warn_only:
        # SZINT-ALAPU CSILLAPITAS, es ez nem finomhangolas: a lenti, futas-alapu
        # ismetlesvedelem warn-only modban NEM MUKODIK. Az azt nezi, irt-e az
        # agens transzkriptet a legutobbi jelzes ota -- egy LEALLITOTT agensnel
        # ez pont a helyes kerdes, mert a leallitas utan csak ujraindulva ir.
        # A fo-agenst viszont senki nem allitja le: dolgozik tovabb, tehat MINDEN
        # korben "ujra aktiv", es minden korben ujra riaszt.
        # MERVE 2026-09-16, elesben, a gazda vette eszre: 11:07, 11:17, 11:28,
        # 11:38, 11:48 -- OT figyelmeztetes 41 perc alatt, ugyanarrol a 78%-rol.
        # Csak a 11:58-as fogta meg magat, mert addigra eppen nem dolgoztam.
        # Ezert itt a csillapitas SAVOKHOZ kotodik: egy sav naponta egyszer szol,
        # es a kovetkezo jelzes csak akkor jon, ha a helyzet tenylegesen romlott.
        savok = [70, 85, 95, 100]
        sav = max([x for x in savok if pct >= x], default=None)
        if sav is None:
            return 0
        ma = datetime.now().strftime("%Y-%m-%d")
        st = load()
        ent = st.get(a.agent) or {}
        kuldott = set(ent.get("warn_levels") or []) if ent.get("warn_day") == ma else set()
        if sav in kuldott:
            print(f"# {a.agent}: a {sav}%-os sav ma mar ki lett kuldve, nem ismetlem",
                  file=sys.stderr)
            return 0
        # The main agent IS the channel: stopping it silences the owner's only
        # way to reach the fleet, so here the ceiling is a warning, not a gate.
        # The repeat guard below still applies -- one message per run, not one
        # every tick.
        # A szokozosites a SZAMOKON fut, nem a kesz uzeneten. A regi alak az EGESZ
        # msg-re futott, tehat a magyar mondatok vesszoit is szetverte ("Kuszob: X  a
        # napi plafon Y"). Anorak vette eszre 2026-09-19 a testverfajlban; ugyanaz a
        # hiba itt HAROM helyen allt. Javitva mind a harmon.
        sz_total, sz_trigger, sz_cap = (f"{total:,}".replace(",", " "),
                                        f"{trigger:,}".replace(",", " "),
                                        f"{a.cap:,}".replace(",", " "))
        msg = (f"{a.agent}: elerted a napi figyelmeztetesi kuszobot.\n\n"
               f"{sz_total} token {ablak}.\n"
               f"Kuszob: {sz_trigger}, a napi plafon {sz_cap}.\n"
               f"Idopont: {when}.\n\n"
               "Ez NEM allitott le semmit: a fo-agenst leallitani annyi, mint a "
               "csatornat elnemitani. A dontes a tied.")
        sent = notify(msg)
        if sent:
            kuldott.add(sav)
            st[a.agent] = {"warn_day": ma, "warn_levels": sorted(kuldott),
                           "last_warn_at": time.time(), "warn_only": True}
            save(st)
        return 0


    if total < trigger:
        return 0

    if override_today(a.agent, a.daily_from):
        print(f"# {a.agent}: kuszob folott ({total}), de mara felmentve ({OVERRIDE}), nem allitom le",
              file=sys.stderr)
        return 0

    st = load()
    ent = st.get(a.agent) or {}
    # Already stopped for this run? Only re-arm once the agent has written
    # transcript lines AFTER the stop, i.e. it is genuinely running again.
    if ent.get("stopped_at") and last <= ent.get("last_activity_at", 0):
        print(f"# {a.agent}: mar leallitva ({int(time.time()-ent['stopped_at'])//60} perce), nem ismetlem",
              file=sys.stderr)
        return 0

    ok = stop_agent(a.agent)
    sz_total, sz_trigger, sz_cap = (f"{total:,}".replace(",", " "),
                                    f"{trigger:,}".replace(",", " "),
                                    f"{a.cap:,}".replace(",", " "))
    msg = (f"{a.agent} le lett allitva a token-plafon ELOTT.\n\n"
           f"{sz_total} token {ablak}.\n"
           f"Leallitasi kuszob: {sz_trigger}, a plafon {sz_cap}.\n"
           f"Idopont: {when}.\n\n"
           "Ez a sajat merese, nem a teljes fiokod. "
           "Ha ma megis futnia kell, szolj Marveennek: mara feloldja. "
           "Egy sima ujrainditas NEM eleg, azt a kovetkezo ellenorzes perceken belul ujra leallitja.")
    if a.daily_from is not None:
        # #663, owner tg 9191: the marveen-<agent>-day-start unit starts it at the
        # anchor hour if agent-has-work.py finds work. True only with a daily
        # window: a rolling window (anorak) has no "next day", only its shift.
        msg += (f"\n\nA kovetkezo napi ablak {a.daily_from:02d}:00-kor nyilik, es ha akkor "
                "van munkaja, a reggeli indito magatol elinditja.")
    if not ok:
        msg = (f"{a.agent} elerte a leallitasi kuszobot, de A LEALLITAS NEM SIKERULT.\n\n"
               f"{sz_total} token {ablak}, a kuszob {sz_trigger}, a plafon {sz_cap}.\n"
               "Kezzel kell leallitani a dashboardon.")
    sent = notify(msg, to_owner=not ok)

    # Record ONLY when the stop actually worked: an uncommitted failure must be
    # retried on the next tick, not buried under a "handled" flag.
    if ok:
        st[a.agent] = {"stopped_at": int(time.time()), "last_activity_at": last,
                       "tokens": total, "cap": a.cap, "trigger": trigger,
                       "notified": sent}
        save(st)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # a guard must never wedge the machine
        print(f"HIBA: {e}", file=sys.stderr)
        sys.exit(2)
