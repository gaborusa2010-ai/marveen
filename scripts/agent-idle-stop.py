#!/usr/bin/env python3
"""Stop a sub-agent that has no work and has been quiet for 30 minutes.

Owner tg 9180 (2026-10-25, card #663 part 3): "egy meghatarozott ido utan (ha nem
jon uj uzenet es nincs munkaja az agentnek) akkor leallitja az agentet";
tg 9183: the quiet time is 30 minutes. tg 9185: Ariel too.

An agent is stopped only when ALL of these hold:
  1. it is a sub-agent (never marveen) and is running (/api/agents + tmux)
  2. agent-has-work.py finds no work (no planned/in_progress/testing card, no
     date-wait running out today, no open message); a read error = keep it
  3. the last sign of life is QUIET_MIN old. Sign of life = the newest write to
     any transcript under its own config (every turn, every delivered message
     and every owner bot message writes one) or the tmux session's creation
     (a just-started agent has no fresh transcript yet)
  4. its pane is not busy ("esc to interrupt") and did not change since the
     previous tick (a background job that prints is still working)
  5. Anorak is left alone inside its shift (22:55-06:35): its own timers own it

A stopped agent is not lost: a scheduled task auto-starts it
(src/web/schedule-runner.ts startAgentProcess), the 06:00 unit starts it when it
has work, and #663 part 2 starts it on an owner message.

Usage:
  agent-idle-stop.py              decide and stop (the 5-minute timer runs this)
  agent-idle-stop.py --dry-run    print the decision per agent, stop nothing
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "store", "agent-idle-stop-state.json")
QUIET_MIN = int(os.environ.get("IDLE_STOP_QUIET_MIN", "30"))
MAIN = "marveen"

_spec = importlib.util.spec_from_file_location(
    "agent_has_work", os.path.join(REPO, "scripts", "agent-has-work.py"))
ahw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ahw)
bb = ahw.bb


def in_anorak_shift(now):
    hm = now.hour * 60 + now.minute
    return hm >= 22 * 60 + 55 or hm < 6 * 60 + 35


def newest_transcript(agent):
    root = os.path.join(REPO, "agents", agent, ".claude-config", "projects")
    newest = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".jsonl"):
                try:
                    newest = max(newest, os.stat(os.path.join(dirpath, f)).st_mtime)
                except OSError:
                    pass
    return newest


def tmux(*args):
    r = subprocess.run(["tmux"] + list(args), capture_output=True, text=True, timeout=20)
    return r.stdout if r.returncode == 0 else None


def session_created(agent):
    out = tmux("display-message", "-p", "-t", "agent-%s:" % agent, "#{session_created}")
    try:
        return int(out.strip())
    except (AttributeError, ValueError):
        return None


def pane(agent):
    return tmux("capture-pane", "-p", "-t", "agent-%s:" % agent)


def decide(agent, *, running, has_tmux, work, last_life, pane_text, prev_hash, now):
    """Pure: (stop?, reason). work=None means it could not be read."""
    if agent == MAIN:
        return False, "fo-agens"
    if not running or not has_tmux:
        return False, "nem fut"
    if agent == "anorak" and in_anorak_shift(datetime.fromtimestamp(now)):
        return False, "anorak muszakban"
    if work is None:
        return False, "a munka nem olvashato"
    if work:
        return False, "van munka: " + ", ".join(work[:3])
    quiet = (now - last_life) / 60
    if quiet < QUIET_MIN:
        return False, "csend %.0f perc < %d" % (quiet, QUIET_MIN)
    if pane_text is None:
        return False, "a pane nem olvashato"
    if "esc to interrupt" in pane_text:
        return False, "a pane dolgozik"
    h = hashlib.sha256(pane_text.encode()).hexdigest()
    if prev_hash != h:
        return False, "a pane valtozott az elozo kor ota"
    return True, "nincs munka, csend %.0f perc, a pane all" % quiet


def main(argv):
    dry = "--dry-run" in argv
    now = time.time()
    try:
        with open(STATE) as f:
            state = json.load(f)
    except Exception:
        state = {}
    tok = bb._token()
    agents = bb._rows(bb._get("/api/agents", tok), "agents")
    sessions = tmux("ls", "-F", "#{session_name}") or ""
    live = {s[len("agent-"):] for s in sessions.split() if s.startswith("agent-")}
    new_state = {}
    for a in agents:
        name = a.get("name") or ""
        if not name or name == MAIN:
            continue
        running = bool(a.get("running"))
        has_tmux = name in live
        if not (running and has_tmux):
            print("%-9s marad: nem fut" % name)
            continue
        try:
            work = ahw.check(name, tok)
        except Exception as e:
            work = None
            print("%-9s a munka nem olvashato: %s" % (name, e))
        created = session_created(name) or now
        last_life = max(newest_transcript(name), created)
        text = pane(name)
        if text is not None:
            new_state[name] = hashlib.sha256(text.encode()).hexdigest()
        stop, why = decide(name, running=running, has_tmux=has_tmux, work=work, last_life=last_life,
                           pane_text=text, prev_hash=state.get(name), now=now)
        if not stop:
            print("%-9s marad: %s" % (name, why))
            continue
        if dry:
            print("%-9s LEALLITANAM (dry-run): %s" % (name, why))
            continue
        r = subprocess.run(["bash", os.path.join(REPO, "scripts", "agent-night-shift.sh"), name, "stop"],
                           capture_output=True, text=True, timeout=120)
        print("%-9s LEALLITVA: %s | %s" % (name, why, (r.stdout or r.stderr).strip().splitlines()[-1:] or ""))
        new_state.pop(name, None)
    # Written on a dry run too: it holds only pane fingerprints, and without it
    # every dry run would answer "the pane changed".
    with open(STATE + ".tmp", "w") as f:
        json.dump(new_state, f)
    os.replace(STATE + ".tmp", STATE)
    return 0


if __name__ == "__main__":
    print(datetime.now().strftime("%F %T"))
    sys.exit(main(sys.argv[1:]))
