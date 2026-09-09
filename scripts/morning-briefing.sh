#!/bin/bash
# Marveen - Reggeli napindító
# Trigger: systemd user timer (Linux, <agent>-morning.timer) vagy LaunchAgent
# (macOS), naponta 7:27-kor. Naponta legfeljebb egyszer küld (lásd a guardot).

export PATH="$HOME/.local/bin:$HOME/.bun/bin:/home/linuxbrew/.linuxbrew/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE="$(command -v claude)"
[ -z "$CLAUDE" ] && echo "ERROR: claude not found on PATH" >&2 && exit 1
LOG="$INSTALL_DIR/store/morning.log"

# Load config
if [ -f "$INSTALL_DIR/.env" ]; then
  export $(grep -v '^#' "$INSTALL_DIR/.env" | xargs)
fi

CHAT_ID="${ALLOWED_CHAT_ID:-0}"
CALENDAR_ID="${HEARTBEAT_CALENDAR_ID:-primary}"

# Same-day dedup guard: the briefing must go out at most once per calendar
# day no matter how many times the trigger fires (a timer-unit re-activation
# on a systemd user-manager restart, a Persistent= catch-up, or a manual
# re-run). MORNING_FORCE=1 bypasses the guard for deliberate re-sends.
STAMP="$INSTALL_DIR/store/.morning-last-sent"
SENT_MARKER="$INSTALL_DIR/store/.morning-sent-marker"
TODAY="$(date +%F)"
if [ "${MORNING_FORCE:-0}" != "1" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$TODAY" ]; then
  echo "=== Reggeli napindító $(date) -- SKIP: ma már elküldve (guard: $STAMP) ===" >> "$LOG"
  exit 0
fi

echo "=== Reggeli napindító $(date) ===" >> "$LOG"

cd "$INSTALL_DIR"

$CLAUDE --dangerously-skip-permissions \
  --channels plugin:telegram@claude-plugins-official \
  -p "Reggeli napindító - készítsd el és küld el Telegramra (chat_id: $CHAT_ID).

1. Email check: search_emails az elmúlt 12 órából, szűrd ki a spam/promo emaileket
2. Naptár: list-events a mai napra a $CALENDAR_ID naptárból (Europe/London timezone)
3. AI hírek: WebSearch \"AI news [tegnapi dátum]\"
4. Küld el Telegramra a reply tool-lal (chat_id: $CHAT_ID)
5. CSAK AKKOR, ha a reply tool visszaigazolta a kikuldest (kapott message id):
   futtasd le ezt, mas esetben SOHA:
   date +%F > $SENT_MARKER

Tomor, lenyegre toro. Ekezetesen irj magyarul." >> "$LOG" 2>&1

# SILENTSTAMP901: the guard used to be stamped on `claude -p` exit 0 alone.
# That exit code says the SESSION ran, not that a briefing was DELIVERED --
# on 2026-09-01 the run only asked the owner a provenance question, sent
# nothing, and still stamped the day as done. Had the scheduled-task path not
# existed as well, the briefing would have been silently skipped while the
# guard claimed success. The stamp now follows the marker the run itself
# writes only after the reply tool confirmed a message id.
if [ "$(cat "$SENT_MARKER" 2>/dev/null)" = "$TODAY" ]; then
  echo "$TODAY" > "$STAMP"
else
  echo "NEM stampelek: a futas nem igazolta vissza a kikuldest ($SENT_MARKER != $TODAY)" >> "$LOG"
fi

echo "=== Kész $(date) ===" >> "$LOG"
