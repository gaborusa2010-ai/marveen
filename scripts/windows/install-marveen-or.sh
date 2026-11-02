#!/usr/bin/env bash
# Installs the Windows-side WSL/Marveen watchdog (#749) from inside WSL:
# copies the scripts to C:\Users\<user>\MarveenOr and registers a Task
# Scheduler task that runs every 5 minutes while the user is logged on.
# The Telegram token is NOT copied here; put the channel's .env there as
# telegram.env (line TELEGRAM_BOT_TOKEN=...). Without it the watchdog only logs.
set -euo pipefail
WINUSER="${WINUSER:-gabor}"
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="/mnt/c/Users/$WINUSER/MarveenOr"
WDEST="C:\\Users\\$WINUSER\\MarveenOr"
mkdir -p "$DEST"
cp "$SRC/marveen-or.ps1" "$SRC/run-hidden.vbs" "$DEST/"
/mnt/c/Windows/System32/schtasks.exe /Create /F /SC MINUTE /MO 5 /TN "Marveen-or" \
  /TR "wscript.exe \"$WDEST\\run-hidden.vbs\"" | tr -d '\r'
/mnt/c/Windows/System32/schtasks.exe /Query /TN "Marveen-or" /FO LIST | tr -d '\r' | grep -E "TaskName|Next Run|Status"
