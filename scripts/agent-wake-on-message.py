#!/usr/bin/env python3
"""Start a stopped sub-agent when the owner writes to it on Telegram.

Owner tg 9180 (2026-10-25, card #663 part 2): an agent without work is stopped
(agent-idle-stop.py, 30 quiet minutes), and "ha irok neki, elindul". A stopped
agent has no poller, so its bot holds the owner's message as a pending update.

HOW IT LOOKS WITHOUT EATING THE MESSAGE
---------------------------------------
getUpdates WITHOUT an offset returns the pending updates and confirms nothing:
the same updates come back on the next call, and the agent's own poller gets
them when it starts (grammy's bot.start() does not drop pending updates;
plugin server.ts 0.0.7). So this script only peeks.

WHEN IT DOES NOT LOOK
---------------------
A bot token has ONE getUpdates slot. A peek while the agent's poller runs ends
that poller's long-poll with 409, and the plugin gives up after 8 of them. So
the peek runs only if the agent is stopped by every measure: /api/agents says
not running, there is no agent-<name> tmux session, and the bot.pid in its
channel dir is not a live process.

WHAT WAKES IT
-------------
  - a private message from a user on the agent's allowFrom list
  - a group message from such a user that names the agent or its bot
    (the team chat reaches every bot; a message for someone else must not start
    all of them -- docs/tudas/csapat-chat.md)

Usage:
  agent-wake-on-message.py              peek and start (the 1-minute timer runs this)
  agent-wake-on-message.py --dry-run    print the decision per agent, start nothing
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.environ.get("MARVEEN_DASHBOARD_URL", "http://localhost:3420")
MAIN = "marveen"


def _token():
    with open(os.path.join(REPO, "store", ".dashboard-token")) as f:
        return f.read().strip()


def _json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def channel_dir(agent):
    return os.path.join(REPO, "agents", agent, ".claude", "channels", "telegram")


def bot_token(agent):
    try:
        with open(os.path.join(channel_dir(agent), ".env")) as f:
            for line in f:
                line = line.strip()
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip() or None
    except OSError:
        return None
    return None


def allow_from(agent):
    try:
        with open(os.path.join(channel_dir(agent), "access.json")) as f:
            return {str(x) for x in json.load(f).get("allowFrom") or []}
    except (OSError, ValueError):
        return set()


def poller_alive(agent):
    try:
        with open(os.path.join(channel_dir(agent), "bot.pid")) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def tmux_sessions():
    r = subprocess.run(["tmux", "ls", "-F", "#{session_name}"], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        # "no server running" means no session at all; anything else is unknown.
        return set() if "no server" in (r.stderr or "") else None
    return {s[len("agent-"):] for s in r.stdout.split() if s.startswith("agent-")}


def wakes(agent, updates, allowed, extra_names=()):
    """Pure: the first update that should start the agent, or None. A group
    message counts when it names the agent (name, display name or nickname)."""
    names = {n.lower() for n in (agent,) + tuple(extra_names) if n and len(n) >= 3}
    for u in updates:
        m = u.get("message") or u.get("edited_message") or {}
        sender = str((m.get("from") or {}).get("id", ""))
        if not sender or sender not in allowed:
            continue
        chat = m.get("chat") or {}
        if chat.get("type") == "private":
            return u
        text = (m.get("text") or m.get("caption") or "").lower()
        if any(n in text for n in names):
            return u
    return None


def main(argv):
    dry = "--dry-run" in argv
    tok = _token()
    agents = _json(DASH + "/api/agents", {"Authorization": "Bearer " + tok})
    if isinstance(agents, dict):
        agents = agents.get("agents") or []
    live = tmux_sessions()
    if live is None:
        print("tmux allapot nem olvashato, ebben a korben senkit nem nezek")
        return 1
    rc = 0
    for a in agents:
        name = a.get("name") or ""
        if not name or name == MAIN:
            continue
        if a.get("running") or name in live or poller_alive(name):
            continue
        bt = bot_token(name)
        if not bt:
            continue
        try:
            resp = _json("https://api.telegram.org/bot%s/getUpdates?timeout=0&limit=100" % bt)
        except Exception as e:
            # The token must never reach a log line: report the class only.
            print("%-9s getUpdates hiba: %s" % (name, type(e).__name__))
            rc = 1
            continue
        u = wakes(name, resp.get("result") or [], allow_from(name),
                  extra_names=(a.get("displayName"), a.get("nickname")))
        if not u:
            continue
        mid = (u.get("message") or u.get("edited_message") or {}).get("message_id")
        if dry:
            print("%-9s INDITANAM (dry-run): tulajdonosi uzenet var, message_id=%s" % (name, mid))
            continue
        r = subprocess.run(["bash", os.path.join(REPO, "scripts", "agent-night-shift.sh"), name, "start"],
                           capture_output=True, text=True, timeout=300)
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-1:] or [""]
        print("%-9s ELINDITVA uzenetre (message_id=%s), exit=%s | %s" % (name, mid, r.returncode, tail[0]))
        rc |= 1 if r.returncode else 0
    return rc


if __name__ == "__main__":
    print(datetime.now().strftime("%F %T"))
    sys.exit(main(sys.argv[1:]))
