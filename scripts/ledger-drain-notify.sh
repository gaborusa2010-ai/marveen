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

# SCOPE, stated rather than left to be discovered (Cody, 2026-09-01): this timer
# covers the MAIN agent ONLY. It cd's to the install root, so the drain resolves
# agent_id=marveen, and it sends to marveen. Sub-agents with their own channel
# (Margo has one) get no such sweep -- a lost inbound of theirs is not picked up
# by anything. That is a decision waiting to be made, not an oversight to be
# assumed: waking five agents on a timer has its own cost, and it needs the
# owner's call. Until then the limit is written here so nobody reads this timer
# as fleet-wide coverage.
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE" || exit 2   # the drain derives its agent_id from the cwd

OUT="$(/usr/bin/python3 "$BASE/scripts/hooks/ledger-live-drain.py" --peek 2>/dev/null)"
[ -z "$OUT" ] && exit 0   # nothing open -- the normal case, zero cost

MSG_ID="$(printf '%s' "$OUT" | head -n1 | sed -n 's/.*message_id=\([^ ]*\).*/\1/p')"
[ -z "$MSG_ID" ] && exit 0

RESULT="$(printf '%s\n' \
  "[ELVESZETT BEJÖVŐ] Ez egy korábban elveszett, még megválaszolatlan üzenet a csatornából." \
  "Olvasd el és válaszolj rá MOST a csatorna-válasz eszközzel, a lenti chat_id-ra," \
  "ugyanúgy mintha most érkezett volna." \
  "" \
  "$OUT" | bash "$BASE/scripts/agent-msg.sh" marveen marveen -)"

case "$RESULT" in
  OK\ id=*)
    /usr/bin/python3 "$BASE/scripts/hooks/ledger-live-drain.py" --commit "$MSG_ID"
    echo "$(date '+%Y-%m-%d %H:%M:%S') delivered message_id=$MSG_ID ($RESULT)"
    ;;
  *)
    # NOT committed on purpose: the next tick retries.
    echo "$(date '+%Y-%m-%d %H:%M:%S') FAIL message_id=$MSG_ID ($RESULT) -- nem commitolva, kovetkezo korben ujra" >&2
    ;;
esac
exit 0
