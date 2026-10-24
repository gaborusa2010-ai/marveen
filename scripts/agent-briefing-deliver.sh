#!/usr/bin/env bash
# agent-briefing-deliver.sh -- morning briefing for agents that are NOT started by a timer.
#
# Why this exists (owner tg 9074, 2026-10-24)
# -------------------------------------------
# The morning list (agent-briefing-build.py) only ever reached anorak, cody,
# forgacs, margo and roxy, because it rides on their day-start unit:
# agent-night-shift.sh <agent> start -> send_night_briefing. Ariel, Brigid,
# Kiron, Merlin and Prospero run all day and have no start unit, so nothing
# built or sent their list -- measured 10-24: they held 15 open cards between
# them (prospero 9, ariel 3, the rest 1 each) and got no briefing at all.
#
# This script NEVER starts or stops an agent. A stopped agent is stopped for a
# reason (budget, owner); waking it to read a list would spend quota on nothing.
# For a stopped agent the built file stays in store/briefing-<agent>.txt, and
# the next start through agent-night-shift.sh delivers it.
#
# The send itself is NOT a second copy: it sources agent-night-shift.sh and
# calls the same send_night_briefing, so a confirmed id retires the file and a
# failed send keeps it, exactly as for the timer-started agents.
#
# Usage: agent-briefing-deliver.sh ariel brigid kiron merlin prospero
set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=agent-night-shift.sh
source "$BASE/scripts/agent-night-shift.sh"

PORT="${MARVEEN_WEB_PORT:-3420}"
TOKEN="$(cat "$BASE/store/.dashboard-token")"
[ "$#" -gt 0 ] || { echo "usage: $0 <agent>..." >&2; exit 2; }

RC=0
for AGENT in "$@"; do
  # Unknown name first: measured 10-24, a typo built store/briefing-<typo>.txt and
  # sent the main agent a "has no work" notice for an agent that does not exist.
  STATE="$(curl -s -m 20 -H "Authorization: Bearer $TOKEN" "http://localhost:$PORT/api/agents" \
    | AGENT="$AGENT" python3 "$BASE/scripts/agent-runstate.py")"
  # "unreadable" = the API did not answer: building now would leave a file that
  # the builder never overwrites, so tomorrow's run would send today's stale list.
  if [ "$STATE" = "missing" ] || [ "$STATE" = "unreadable" ] || [ -z "$STATE" ]; then
    echo "$(date '+%F %T') $AGENT ismeretlen agens vagy elerhetetlen API (runState='$STATE'), kihagyom" >&2
    RC=1
    continue
  fi
  # '-' semantics as in the day-start units: a build failure must not stop the
  # other agents' lists. The builder never overwrites a hand-written file.
  python3 "$BASE/scripts/agent-briefing-build.py" "$AGENT" \
    || echo "$(date '+%F %T') $AGENT briefing build HIBA, a meglevo fajl (ha van) megy" >&2

  # Running = BOTH sources agree, the same rule as the start confirmation.
  STATE="$(curl -s -m 20 -H "Authorization: Bearer $TOKEN" "http://localhost:$PORT/api/agents" \
    | AGENT="$AGENT" python3 "$BASE/scripts/agent-runstate.py")"
  TMUX_OK=nem
  tmux ls 2>/dev/null | grep -q "agent-$AGENT:" && TMUX_OK=igen
  if [ "$STATE" != "running" ] || [ "$TMUX_OK" != "igen" ]; then
    echo "$(date '+%F %T') $AGENT nem fut (runState=$STATE tmux=$TMUX_OK): a lista a fajlban var a kovetkezo inditasig"
    continue
  fi
  # Third arg "": the agent was not just started, but a file was built above,
  # so the send branch is the one that runs.
  send_night_briefing "$BASE" "$AGENT" "" || RC=1
done
exit "$RC"
