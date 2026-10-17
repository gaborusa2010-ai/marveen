#!/usr/bin/env bash
# Weekly self-test for the voice stack: Piper synthesizes a known Hungarian
# sentence, faster-whisper transcribes it straight back, and the transcript
# is compared to the expected text. Purely local -- no Telegram/network call,
# never touches stt.sh/tts.sh's live state. Exits 0 on pass, 1 on fail/mismatch.
# Usage: canary.sh [voice] [expected text...]   (voice: imre|anna|<path-to-onnx>, default imre)
set -euo pipefail
# Resolve the toolkit dir the same way stt.sh does: it must contain BOTH
# _vtools.py AND a usable venv. The source tree has _vtools.py but no venv, so
# picking it on _vtools.py alone made a repo run print "skip" and exit 0 even
# with the stack installed -- a silent false pass.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${MARVEEN_VOICE_DIR:-$HOME/.local/share/marveen-voice}"
if [[ -f "$SCRIPT_DIR/_vtools.py" && -x "$SCRIPT_DIR/venv/bin/python" ]]; then
  DEST="$SCRIPT_DIR"
else
  DEST="$INSTALL_DIR"
fi
# Graceful skip when the (opt-in) voice stack is not installed: a missing venv
# must read as "nothing to test", not as a weekly false-alarm regression.
if [[ ! -x "$DEST/venv/bin/python" ]]; then
  echo "skip: voice stack not installed (no venv at $DEST)"
  exit 0
fi
VOICE_ARG="${1:-imre}"
if [[ $# -gt 0 ]]; then shift; fi
# Per-voice default: measured with noise 0 (deterministic), these score 1.0
# (imre) and 0.86 (anna); the old shared sentence scored 0.71 on imre.
case "$VOICE_ARG" in
  anna) DEFAULT_TEXT="Ez egy heti hangteszt, minden rendben van." ;;
  *)    DEFAULT_TEXT="Minden rendben van, a rendszer működik." ;;
esac
TEXT="${*:-$DEFAULT_TEXT}"
case "$VOICE_ARG" in
  imre)  ONNX="$DEST/voices/hu_HU-imre-medium.onnx" ;;
  anna)  ONNX="$DEST/voices/hu_HU-anna-medium.onnx" ;;
  /*)    ONNX="$VOICE_ARG" ;;
  *)     echo "Unknown voice alias: $VOICE_ARG (use: imre, anna, or absolute path)" >&2; exit 1 ;;
esac
exec "$DEST/venv/bin/python" "$DEST/_vtools.py" canary "$ONNX" "$TEXT"
