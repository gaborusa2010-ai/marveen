#!/bin/bash
# Windows host drive (C:) free-space watch (systemd --user timer, every 5 min).
#
# Gap it closes (2026-11-01): C: filled 14:42-19:58 and WSL went down for ~5 h.
# disk-space-guard.sh only watches the Linux root fs, which lives INSIDE the
# ext4.vhdx on C: and showed 28% used the whole time -- the vhdx grows on C:
# while "/" still looks empty. Owner tg 10360: keep C: from filling again.
#
# Two levels, both with a cooldown:
#   free <= WARN_GB  -> inter-agent message to marveen (act before it is urgent)
#   free <= ALERT_GB -> ALSO the owner over the direct Bot API (the session may be
#                       about to die, so not via MCP)
# Every run appends "<epoch> <freeGB>" to the trend log (last 3000 lines kept),
# so growth rate can be read back later.
#
# Test hooks (env): HOST_DISK_FREE_OVERRIDE (integer GB instead of df),
# HOST_DISK_STATE_DIR (stamps + trend log), HOST_DISK_DRYRUN=1 (print, no send).

set -u

DISK_PATH="/mnt/c"
WARN_GB=35
ALERT_GB=20
WARN_COOLDOWN=21600    # 6 h
ALERT_COOLDOWN=3600    # 1 h

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${HOST_DISK_STATE_DIR:-$INSTALL_DIR/store}"
TREND_LOG="$STATE_DIR/host-disk-free.log"
WARN_STAMP="$STATE_DIR/.host-disk-warned"
ALERT_STAMP="$STATE_DIR/.host-disk-alerted"
TG_CHAN_DIR="${TELEGRAM_STATE_DIR:-}"
if [ -z "$TG_CHAN_DIR" ]; then
  TG_CHAN_DIR="$INSTALL_DIR/.claude/channels/telegram"
  [ -f "$TG_CHAN_DIR/.env" ] || TG_CHAN_DIR="$HOME/.claude/channels/telegram"
fi
TG_ENV="$TG_CHAN_DIR/.env"

. "$INSTALL_DIR/scripts/lib/owner-chat.sh"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [host-disk-watch] $*" || true; }

free_gb() {
  if [ -n "${HOST_DISK_FREE_OVERRIDE:-}" ]; then echo "$HOST_DISK_FREE_OVERRIDE"; return; fi
  df -P -BG "$DISK_PATH" 2>/dev/null | awk 'NR==2 {gsub("G","",$4); print $4}'
}

# 0 if the stamp is older than the cooldown (or missing).
cooldown_over() {
  local stamp="$1" cooldown="$2" last now
  now="$(date +%s)"; last=0
  [ -f "$stamp" ] && last="$(cat "$stamp" 2>/dev/null || echo 0)"
  case "$last" in (''|*[!0-9]*) last=0;; esac
  [ $(( now - last )) -ge "$cooldown" ]
}

notify_marveen() {
  if [ "${HOST_DISK_DRYRUN:-}" = "1" ]; then echo "DRYRUN marveen: $1"; return 0; fi
  local out
  out="$(bash "$INSTALL_DIR/scripts/agent-msg.sh" marveen marveen "$1" 2>&1)"
  case "$out" in (*"OK id="*) log "marveen notified ($out)"; return 0;; esac
  log "marveen notify FAILED: $out"; return 1
}

alert_owner() {
  if [ "${HOST_DISK_DRYRUN:-}" = "1" ]; then echo "DRYRUN owner: $1"; return 0; fi
  local token chat send_err
  token="$(grep -E '^TELEGRAM_BOT_TOKEN=' "$TG_ENV" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r ')"
  chat="$(resolve_owner_chat_id "$INSTALL_DIR/.env")" || chat=""
  if [ -z "$token" ] || [ -z "$chat" ]; then
    log "owner alert skipped: no bot token or owner chat id"; return 1
  fi
  . "$INSTALL_DIR/scripts/lib/send-telegram.sh"
  if send_err="$(send_telegram_message "$token" "$chat" "$1" 2>&1)"; then
    log "owner alerted (delivery confirmed)"; return 0
  fi
  log "owner alert FAILED: $send_err"; return 1
}

main() {
  local free now
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  free="$(free_gb)"
  case "$free" in (''|*[!0-9]*) log "could not read free space of $DISK_PATH (got '$free') -- no-op"; return 0;; esac
  now="$(date +%s)"
  { echo "$now $free" >> "$TREND_LOG"; } 2>/dev/null || true
  if [ "$( { wc -l < "$TREND_LOG"; } 2>/dev/null || echo 0)" -gt 3500 ]; then
    tail -n 3000 "$TREND_LOG" > "$TREND_LOG.tmp" 2>/dev/null && mv "$TREND_LOG.tmp" "$TREND_LOG"
  fi

  [ "$free" -gt "$WARN_GB" ] && return 0

  if [ "$free" -le "$ALERT_GB" ] && cooldown_over "$ALERT_STAMP" "$ALERT_COOLDOWN"; then
    if alert_owner "A C: meghajtón már csak ${free} GB szabad hely van. Ha betelik, a csapat leáll (mint 11-01-én). Marveen most szabadít fel helyet, de ha nem válaszol, ez a jelzés a gépről jött."; then
      { echo "$now" > "$ALERT_STAMP"; } 2>/dev/null || true
    fi
  fi
  if cooldown_over "$WARN_STAMP" "$WARN_COOLDOWN"; then
    if notify_marveen "[C-LEMEZ] C: free ${free} GB (warn <= ${WARN_GB}, owner alert <= ${ALERT_GB}). Trend: store/host-disk-free.log. Find what grows (vhdx, backups staging, Windows), free space, tell the owner (tg 10360)."; then
      { echo "$now" > "$WARN_STAMP"; } 2>/dev/null || true
    fi
  fi
}

main "$@"
exit 0
