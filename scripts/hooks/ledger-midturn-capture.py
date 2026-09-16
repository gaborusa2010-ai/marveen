#!/usr/bin/env python3
"""Stop hook: back-fill the ledger with channel messages that arrived MID-TURN.

MIDTURN901 (measured 2026-09-01, in the closing sweep of the three-round audit).

`ledger-capture.py` runs on UserPromptSubmit, so it only ever sees a message that
STARTED a turn. A message that lands while the agent is already working is
injected into the running turn instead -- and nothing recorded it: not the hook,
not the service (src/db.ts only creates and reads conversation_log; no writer).

Measured on one day's transcript: message ids 1285, 1292, 1295, 1301, 1316 appear
as BOTH `user` and `queue-operation` entries -- those started a turn and were
captured. Ids 1289, 1297, 1304, 1306, 1310 appear ONLY as `queue-operation` --
they arrived mid-turn, and NONE of them reached conversation_log.

Why that matters more than the count: the lost-inbound drain reads that table.
So the safety net was blind exactly for messages that arrive while the agent is
busy, which is when losing one is most expensive. If the session had died right
after one, there would have been no trace of it anywhere.

This hook closes it from the other end: at the end of the turn, read the session
transcript, find every channel envelope, and write the ones that are missing.
Idempotent by construction -- conversation_log has
UNIQUE(agent_id, chat_id, direction, message_id) and the insert is INSERT OR
IGNORE -- so re-running it costs nothing and can never duplicate a row.

FAIL-OPEN ON PURPOSE: a Stop hook that throws would break the turn. Every failure
path exits 0. The cost of a missed back-fill is one unrecorded message; the cost
of a broken Stop hook is every turn.
"""
import sys
import os
import json
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger_lib  # noqa: E402

# Same envelope shape as ledger-capture.py, and the same reason for matching the
# SHAPE rather than one provider: a new channel plugin must not silently vanish.
CHANNEL_RX = re.compile(
    r'<channel\s+source="plugin:[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+"([^>]*)>(.*?)</channel>',
    re.DOTALL,
)

# Entry types that carry a REAL inbound. `queue-operation` is the one that holds
# a mid-turn arrival; `user` holds the turn-starting form. Assistant entries are
# deliberately excluded: this agent quotes channel blocks in its own text all day
# (reports, comments, hand-overs), and treating those as inbound would file the
# agent's own words as the owner's message.
INBOUND_TYPES = ("user", "queue-operation")

# A transcript grows without bound; only the tail can contain this turn. 8 MB is
# far more than one turn and still cheap to scan with a substring pre-filter.
TAIL_BYTES = 8 * 1024 * 1024


def _attr(attrs, name):
    m = re.search(name + r'="([^"]*)"', attrs)
    return m.group(1) if m else None


def _epoch(ts):
    """The envelope ts (ISO-8601 UTC, `...Z`) as a unix epoch, or None.

    MIDTURN901, caught in testing before this hook was registered: stamping a
    back-filled row with the BACK-FILL time puts a two-hour-old message after the
    reply that already answered it. The ledger then reads as "newest inbound,
    unanswered" and the drain raises something already handled -- the exact
    stale-alert failure Roxy measured earlier the same day.
    """
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        return int(datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
                   .astimezone(timezone.utc).timestamp())
    except Exception:
        return None


def _texts(entry):
    """Every string in an entry that could hold the envelope."""
    msg = entry.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                yield part["text"]
    for key in ("content", "text", "prompt"):
        value = entry.get(key)
        if isinstance(value, str):
            yield value


def _tail_lines(path):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size > TAIL_BYTES:
            fh.seek(size - TAIL_BYTES)
            fh.readline()          # drop the partial first line
        for raw in fh:
            # Cheap pre-filter: JSON-parse only the lines that can possibly match.
            if b"<channel source=" not in raw:
                continue
            yield raw.decode("utf-8", errors="replace")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    path = payload.get("transcript_path")
    if not path or not os.path.isfile(path):
        sys.exit(0)

    try:
        agent_id = ledger_lib.agent_id_from_payload(payload)
    except Exception:
        sys.exit(0)

    try:
        for line in _tail_lines(path):
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("type") not in INBOUND_TYPES:
                continue
            for text in _texts(entry):
                for match in CHANNEL_RX.finditer(text):
                    attrs, body = match.group(1), match.group(2)
                    chat_id = _attr(attrs, "chat_id")
                    message_id = _attr(attrs, "message_id")
                    if not (chat_id and message_id):
                        continue
                    att_kind = _attr(attrs, "attachment_kind")
                    att_path = _attr(attrs, "image_path")
                    if att_path and not att_kind:
                        att_kind = "photo"
                    ts = _attr(attrs, "ts")
                    try:
                        ledger_lib.log_inbound(
                            agent_id, chat_id, message_id, body.strip(), ts,
                            attachment_kind=att_kind,
                            attachment_file_id=_attr(attrs, "attachment_file_id"),
                            attachment_path=att_path,
                            created_at=_epoch(ts),
                        )
                    except Exception:
                        pass
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
