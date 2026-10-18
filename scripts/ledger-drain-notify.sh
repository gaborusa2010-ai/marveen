#!/usr/bin/env bash
# ledger-drain-notify.sh -- standalone runner for the lost-inbound drain.
#
# WHY THIS EXISTS
# ---------------
# The drain used to run as a scheduled task every two minutes, which meant it
# woke the model 720 times a day to run a check that is almost always empty --
# and every wake-up re-sends the whole conversation. Measured on 2026-08-31:
# eight consecutive empty rounds in a row, in front of the owner.
#
# The check itself is one SQLite read and costs nothing. Only the WAKING is
# expensive. So this wrapper does the check outside the model and only wakes it
# when there is genuinely an unanswered inbound. Same shape as
# auction-closing-watch.py and limit-monitor.sh: silent until it has something.
#
# WHY --peek AND ONLY THEN --commit
# ---------------------------------
# The drain marks a question "surfaced" so it is not re-raised every tick. In
# the old design printing and seeing were the same act. Here they are two
# processes, so committing at print time would mean: delivery fails, the state
# says handled, and the one lost message is lost for good. So: peek, deliver,
# and commit ONLY after the router returned an id. A failed delivery is retried
# on the next tick. This is the same trap scripts/agent-msg.sh exists to close.
#
# Exit 0 always unless the environment is broken (2).
set -uo pipefail

# SCOPE: the main agent AND every sub-agent with its own Telegram bot (#102,
# owner tg 8305 "Igen, mind a 9-nél", 2026-10-18). Until then this timer covered
# the main agent only (Cody's note, 2026-09-01), and a sub-agent's lost inbound
# was picked up by nothing. The drain derives agent_id from the cwd, so each
# agent is drained from its own agents/<name> dir and the notice goes to THAT
# agent. Still zero model cost when nothing is open: one SQLite read per agent.
# A sub-agent that is not running is skipped WITHOUT commit -- a message to it
# would sit undelivered and fail after an hour -- so its question is raised on
# the first tick after it starts (ledger-replay also shows it at SessionStart).
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE" || exit 2

# drain_one <dir> <agent>: peek in <dir> (cwd -> agent_id), deliver to <agent>,
# commit only after the router returned an id.
drain_one() {
  local dir="$1" agent="$2" out msg_id result
  out="$(cd "$dir" && /usr/bin/python3 "$BASE/scripts/hooks/ledger-live-drain.py" --peek 2>/dev/null)"
  [ -z "$out" ] && return 0   # nothing open -- the normal case, zero cost
  msg_id="$(printf '%s' "$out" | head -n1 | sed -n 's/.*message_id=\([^ ]*\).*/\1/p')"
  [ -z "$msg_id" ] && return 0
  result="$(printf '%s\n' \
    "[ELVESZETT BEJÖVŐ] Ez egy korábban elveszett, még megválaszolatlan üzenet a csatornából." \
    "Olvasd el és válaszolj rá MOST a csatorna-válasz eszközzel, a lenti chat_id-ra," \
    "ugyanúgy mintha most érkezett volna." \
    "" \
    "$out" | bash "$BASE/scripts/agent-msg.sh" marveen "$agent" -)"
  case "$result" in
    OK\ id=*)
      (cd "$dir" && /usr/bin/python3 "$BASE/scripts/hooks/ledger-live-drain.py" --commit "$msg_id")
      echo "$(date '+%Y-%m-%d %H:%M:%S') $agent delivered message_id=$msg_id ($result)"
      ;;
    *)
      # NOT committed on purpose: the next tick retries.
      echo "$(date '+%Y-%m-%d %H:%M:%S') $agent FAIL message_id=$msg_id ($result) -- nem commitolva, kovetkezo korben ujra" >&2
      ;;
  esac
}

drain_one "$BASE" marveen

# Sub-agents with a bot of their own, and only the running ones. If the agent
# list cannot be read, no sub-agent is drained this tick (and nothing committed).
RUNNING="$(curl -s -m 10 -H "Authorization: Bearer $(cat "$BASE/store/.dashboard-token" 2>/dev/null)" \
  http://localhost:3420/api/agents 2>/dev/null | /usr/bin/python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    d = d.get("agents", d) if isinstance(d, dict) else d
    print(" ".join(a["name"] for a in d if a.get("running")))
except Exception:
    pass
' 2>/dev/null)"
for envf in "$BASE"/agents/*/.claude/channels/telegram/.env; do
  [ -f "$envf" ] || continue
  dir="${envf%/.claude/channels/telegram/.env}"
  agent="${dir##*/}"
  case " $RUNNING " in *" $agent "*) drain_one "$dir" "$agent" ;; esac
done
exit 0
