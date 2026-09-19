#!/bin/bash
# PreToolUse hook: auto-resize channel-received large images before Read.
# Works for any channel provider (Telegram, Slack, etc.) whose plugin
# stores received images under ~/.claude/channels/<provider>/inbox/.
#
# Protection: a >500KB image base64-encoded into the context window would
# force a /compact. Instead this hook:
#   1. Copies the original to `inbox/original/<filename>` (if not there)
#   2. Resizes the inbox copy to max 1024x1024 (sips on macOS)
#   3. Reports the original path via additionalContext so the agent can
#      explicitly read full-res when needed (OCR, detail inspection).
#
# Trigger: PreToolUse hook on the Read tool. Only fires when:
#   - tool_name == "Read"
#   - file_path matches /channels/*/inbox/X.{jpg|jpeg|png|gif|webp}
#     (NOT /inbox/original/X -- originals are left alone)
#   - file_size > 500KB

set -u

INPUT=$(cat)
# NO jq. It is not installed on this machine (CLAUDE.md says so, and it is
# measured): `jq -r` then exits 127, both variables come back EMPTY, and the
# tool_name check below exits 0 -- so the hook was registered, "passed" every
# time, and protected nothing. Verified 2026-09-19 with a 600 KB inbox file:
# exit 0, no original/ directory created. python3 is always present.
PARSED=$(printf '%s' "$INPUT" | python3 -c '
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
ti = d.get("tool_input") or {}
print(d.get("tool_name") or "")
print(ti.get("file_path") or "")
' 2>/dev/null)
TOOL_NAME=$(printf '%s' "$PARSED" | sed -n '1p')
FILE_PATH=$(printf '%s' "$PARSED" | sed -n '2p')

# Csak Read tool-ra reagáljunk
[ "$TOOL_NAME" = "Read" ] || exit 0

# Skip if path is already in the `original/` subfolder
case "$FILE_PATH" in
  */channels/*/inbox/original/*) exit 0 ;;
esac

# Match any channel provider inbox image (top-level only)
case "$FILE_PATH" in
  */channels/*/inbox/*.jpg|*/channels/*/inbox/*.jpeg|\
  */channels/*/inbox/*.png|*/channels/*/inbox/*.gif|\
  */channels/*/inbox/*.webp) ;;
  *) exit 0 ;;
esac

# File léteznie kell
[ -f "$FILE_PATH" ] || exit 0

# Méret-check: csak ha >500KB
SIZE=$(stat -f%z "$FILE_PATH" 2>/dev/null || stat -c%s "$FILE_PATH" 2>/dev/null || echo 0)
if [ "$SIZE" -le 524288 ]; then
  exit 0
fi

# Original mentés a /original/ subfolder-be (ha még nincs ott)
INBOX_DIR=$(dirname "$FILE_PATH")
ORIG_DIR="$INBOX_DIR/original"
mkdir -p "$ORIG_DIR" 2>/dev/null
ORIG_PATH="$ORIG_DIR/$(basename "$FILE_PATH")"
if [ ! -f "$ORIG_PATH" ]; then
  cp "$FILE_PATH" "$ORIG_PATH" 2>/dev/null || true
fi

# Resize to max 1024x1024, longest side, aspect kept.
#
# `sips` is macOS-only and this fleet runs on Linux, so on its own it meant the
# resize never happened here -- the half of the hook that actually protects the
# context window. The chain below tries what each platform really has; the
# Pillow venv is the one that works on this machine (measured 2026-09-19).
RESIZED=no
if command -v sips >/dev/null 2>&1; then
  sips -Z 1024 "$FILE_PATH" >/dev/null 2>&1 && RESIZED=yes
fi
if [ "$RESIZED" = no ]; then
  for PY in /home/gabor/.marveen-roxy-venv/bin/python python3; do
    command -v "$PY" >/dev/null 2>&1 || [ -x "$PY" ] || continue
    if "$PY" - "$FILE_PATH" <<'PY' >/dev/null 2>&1; then RESIZED=yes; break; fi
import sys
from PIL import Image
p = sys.argv[1]
im = Image.open(p)
im.thumbnail((1024, 1024))
im.save(p)
PY
  done
fi
if [ "$RESIZED" = no ] && command -v magick >/dev/null 2>&1; then
  magick "$FILE_PATH" -resize 1024x1024\> "$FILE_PATH" >/dev/null 2>&1 && RESIZED=yes
fi

NEW_SIZE=$(stat -f%z "$FILE_PATH" 2>/dev/null || stat -c%s "$FILE_PATH" 2>/dev/null || echo 0)

# Debug log
echo "[channel-image-resize] $FILE_PATH: ${SIZE}B -> ${NEW_SIZE}B (resized=${RESIZED}); original kept at $ORIG_PATH" >&2

# The note must match what HAPPENED. The old text said "was auto-resized"
# unconditionally, so on a box with no resizer it told the agent the context was
# protected while the full-size image went straight into it.
if [ "$RESIZED" = yes ]; then
  NOTE="Note: this channel-received image was auto-resized to max 1024x1024 to protect the context window (was ${SIZE}B, now ${NEW_SIZE}B). The full-resolution original is preserved at: $ORIG_PATH -- Read that path if you need detailed analysis (OCR, fine detail inspection, image editing pre-process)."
else
  NOTE="WARNING: this channel-received image is ${SIZE}B and could NOT be resized -- no working resizer was found on this machine, so reading it will put the full-size image into the context window. A copy is preserved at: $ORIG_PATH. Consider resizing it yourself before Read, or read it only if you truly need it."
fi

python3 - "$NOTE" <<'PY'
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": sys.argv[1],
}}))
PY

exit 0
