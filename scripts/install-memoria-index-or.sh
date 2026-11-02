#!/bin/bash
# Installs the memory-index sentinel (scripts/memoria-index-or.py, card #763) as a
# systemd USER timer, the same shape as marveen-kikapcsolas-lejarat.{timer,service}.
# Idempotent: rewrites the two unit files, reloads, enables. Run as the user that
# owns ~/.config/systemd/user (the main agent / the owner), not from a sub-agent.
#
#   bash scripts/install-memoria-index-or.sh              # install + enable --now
#   bash scripts/install-memoria-index-or.sh --write-only # write units, no systemctl (tests)
#   UNIT_DIR=/tmp/x bash scripts/install-memoria-index-or.sh --write-only
set -eu
DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${UNIT_DIR:-$HOME/.config/systemd/user}"
NAME=marveen-memoria-index-or
PY="$(command -v python3)"
WRITE_ONLY=0
[ "${1:-}" = "--write-only" ] && WRITE_ONLY=1

mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/$NAME.service" <<EOF
[Unit]
Description=Memory index sentinel: alert when the shared MEMORY.md nears the load limit (card #763)

[Service]
Type=oneshot
ExecStart=$PY $DIR/scripts/memoria-index-or.py
EOF
cat > "$UNIT_DIR/$NAME.timer" <<EOF
[Unit]
Description=Daily 05:40: measure the shared memory index and alert above target (card #763)

[Timer]
OnCalendar=*-*-* 05:40
Persistent=true

[Install]
WantedBy=timers.target
EOF
echo "written: $UNIT_DIR/$NAME.service, $UNIT_DIR/$NAME.timer"
[ "$WRITE_ONLY" = 1 ] && exit 0

systemctl --user daemon-reload
systemctl --user enable --now "$NAME.timer"
systemctl --user list-timers "$NAME.timer" --no-pager
