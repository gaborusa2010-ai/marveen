#!/usr/bin/env bash
# Weekly voice canary (#94). Runs canary.sh for both voices against the
# INSTALLED toolkit; silent on pass, one inter-agent message to the main agent
# on fail. A missing voice stack is a pass (canary.sh prints skip, exit 0).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALL_DIR="${MARVEEN_VOICE_DIR:-$HOME/.local/share/marveen-voice}"
LOG="$REPO/store/voice-canary.log"
fail=""
for v in imre anna; do
  out="$(bash "$INSTALL_DIR/canary.sh" "$v" 2>&1)"; rc=$?
  out="$(printf '%s' "$out" | tail -3)"
  printf '%s %s rc=%s %s\n' "$(date -Is)" "$v" "$rc" "$(printf '%s' "$out" | tail -1)" >> "$LOG"
  [[ "$rc" -eq 0 ]] || fail+="$v rc=$rc: $out"$'\n'
done
if [[ -n "$fail" ]]; then
  printf '[voice-canary] heti hangteszt BUKOTT (#94):\n%s\nNaplo: store/voice-canary.log' "$fail" \
    | bash "$REPO/scripts/agent-msg.sh" marveen marveen -
  exit 1
fi
