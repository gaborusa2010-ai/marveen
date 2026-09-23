#!/usr/bin/env python3
"""Live-session open-question drain (heartbeat-driven, NOT a Claude Code hook).

The SessionStart replay (ledger-replay.py) only re-surfaces the open question on
a *respawn*. But a message can be lost in an ALREADY-RUNNING session (a
mid-session channel-deafness gap): ledger-capture.py still records it in
conversation_log, yet the running session never sees it until the next respawn.

This drain runs every ~2 min in the live session (scheduled task
ledger-live-drain) and re-surfaces the still-unanswered inbound so the running
agent answers it WITHOUT waiting for a respawn.

Deterministic + safe:
- GRACE: only surface an open question older than NOW-180s, so we never fight an
  in-flight reply the agent is composing right this moment.
- DEDUP: a statefile next to the ledger DB holds the last surfaced message_id;
  re-surfacing the same id is suppressed (backstop for the surface->reply window,
  so a single missed question is surfaced ONCE, not every tick).
- Never blocks: ANY error -> exit 0, silent (a drain failure must never wedge the
  session or emit noise).

agent_id is derived from the process cwd (generic across channel agents), so the
drain only ever surfaces THIS agent's own open question. When it surfaces one it
writes exactly this block to stdout:

    OPEN_QUESTION chat_id=<id> message_id=<id>
    <text>

With --precheck (the task's scheduler preCheck, via ledger-live-drain-precheck.sh)
it prints "SKIP" when the drain would surface nothing and nothing at all when it
would, and never writes the statefile. An idle install then spends no model turn
on the ~720 empty ticks a day.
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger_lib  # noqa: E402

# GRACE901 (Roxy, 2026-09-01, measured on nine real raises). At 60s the drain
# was RIGHT every time -- nine raises, nine distinct message ids, zero repeats --
# but in three of them the answer went out 13-34 seconds AFTER the raise, so by
# the time the notice was read it described a solved problem. A correct alert
# that arrives stale trains the reader to skip alerts, and then the real lost
# message goes past too.
# Her numbers, response delay measured from arrival:
#   1m51, 2m33, 2m46, 2m51  <- these were being answered anyway
#   4m33, 5m25, 6m51, 8m02, 8m44  <- here the raise actually mattered
# 180s drops all four of the near-misses and keeps all five that counted.
# STATED UNCERTAINTY, hers and worth keeping: for the 13-34s cases we cannot
# tell whether the answer was already being typed or whether the RAISE prompted
# it. The five cases past four minutes carry no such doubt.
GRACE_SECONDS = 180


def _statefile(agent_id):
    """Last-surfaced-id marker, kept beside the ledger DB so it is isolated in
    tests (LEDGER_DB_PATH override) and lands in store/ in production."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(agent_id))
    return os.path.join(os.path.dirname(ledger_lib.db_path()), f".ledger-drain-{safe}")


def _last_surfaced(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def _record_surfaced(path, message_id):
    try:
        with open(path, "w") as f:
            f.write(str(message_id))
    except Exception:
        pass


def main():
    # --peek / --commit exist for the STANDALONE runner (systemd timer +
    # scripts/ledger-drain-notify.sh), which prints in one process and delivers
    # in another. Recording "surfaced" at print time is right for the heartbeat
    # -- there, printing IS the agent seeing it -- but wrong for the standalone
    # path: if the delivery then fails, the only unanswered question is marked
    # handled and is never surfaced again. Same rule as auction-closing-watch:
    # a failed send is not committed. No flags = the old behaviour, unchanged.
    peek = "--peek" in sys.argv
    commit_id = None
    if "--commit" in sys.argv:
        i = sys.argv.index("--commit")
        if i + 1 < len(sys.argv):
            commit_id = sys.argv[i + 1]
    # --precheck: the scheduler asks "is there anything to surface?" BEFORE it
    # spends a model turn (runPreCheck contract: "SKIP" = no turn). It answers
    # with exactly the same rules as a real drain but never records anything,
    # because the scheduler's busy gate runs after it and may still drop the
    # tick; a question marked surfaced here would then be deduplicated away.
    precheck = "--precheck" in sys.argv[1:]

    def nothing():
        if precheck:
            sys.stdout.write("SKIP\n")
        sys.exit(0)

    agent_id = ledger_lib.agent_id_from_cwd(os.getcwd())

    if commit_id is not None:
        _record_surfaced(_statefile(agent_id), commit_id)
        sys.exit(0)

    try:
        oq = ledger_lib.open_question_with_age(agent_id)
    except Exception:
        nothing()  # ledger unavailable -> silent no-op
    if not oq:
        nothing()  # nothing open (none, or already answered)
    # Prefix-slice on purpose (HOOKARITAS821): this unpack sits outside the
    # try above, so a widened open_question_with_age() tuple would kill the
    # hook with a ValueError -- and a dead drain hook fails OPEN: the unanswered
    # question is simply never surfaced. The reply guard died exactly this way
    # for ten days (#1028).
    chat_id, message_id, text, ts, created_at, att_kind, att_file_id = oq[:7]

    # GRACE: skip a fresh inbound the agent may be answering right now.
    try:
        if created_at is None or (int(time.time()) - int(created_at)) < GRACE_SECONDS:
            nothing()
    except Exception:
        nothing()

    # DEDUP: surface a given message_id at most once.
    path = _statefile(agent_id)
    if _last_surfaced(path) == str(message_id):
        nothing()
    if precheck:
        sys.exit(0)  # something to surface: empty stdout lets the turn run

    snippet = (text or "").strip()
    if att_file_id:
        snippet += (
            f'\n[Ez egy {att_kind or "voice"} csatolmány átirat nélkül: töltsd le '
            f'és írasd át (voice-message-transcribe skill, '
            f'attachment_file_id="{att_file_id}") mielőtt válaszolsz.]'
        )
    sys.stdout.write(f"OPEN_QUESTION chat_id={chat_id} message_id={message_id}\n{snippet}\n")
    if not peek:
        _record_surfaced(path, message_id)
    sys.exit(0)


if __name__ == "__main__":
    main()
