#!/bin/bash
# Contract tests for FALLBACKVAK901: the "guaranteed" Telegram fallback used to
# lose the owner's final answer, and log success while doing it.
# Run: bash scripts/__tests__/telegram-fallback-lost-answer.test.sh
#
# Three defects locked out here:
#   1. api() never checked the "ok" field. Telegram refuses with HTTP 200 +
#      {"ok":false} (bot blocked, chat not found, message too long), so urlopen
#      did not raise, the try/except never fired, and a refusal counted as a
#      delivery. This is the `curl exit 0` trap in python.
#   2. The pending state was removed unconditionally after the fallback attempt,
#      so a failed send destroyed the only record that an answer was owed. No
#      retry was possible: the answer was gone for good.
#   3. The watchdog's deliver() returned "send-failed" correctly and the caller
#      threw it away -- os.remove(path) ran regardless.
#
# All Bot API traffic goes to a local stub via TELEGRAM_API_BASE, so the tests
# are hermetic. The stub's verdict is switchable through a control file, which
# is what lets us reproduce an ok:false refusal without a real blocked bot.

set -u

PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (expected '$2', got '$3')"; fi; }

INSTALL_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
HOOKS_DIR="$INSTALL_DIR/scripts/hooks"
STOP_HOOK="$HOOKS_DIR/telegram_progress_clear.py"
WATCHDOG="$HOOKS_DIR/telegram_progress_watchdog.py"

TMP="$(mktemp -d)"
trap 'kill "$STUB_PID" 2>/dev/null; rm -rf "$TMP"' EXIT

REQLOG="$TMP/requests.log"
PORTFILE="$TMP/port"
VERDICT="$TMP/verdict"        # "ok" or "refuse"
echo ok > "$VERDICT"

cat > "$TMP/stub.py" <<'PYEOF'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
reqlog, portfile, verdict = sys.argv[1], sys.argv[2], sys.argv[3]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode("utf-8") if n else ""
        method = self.path.rsplit("/", 1)[-1]
        with open(reqlog, "a", encoding="utf-8") as f:
            f.write(f"{method} {body}\n")
        try:
            mode = open(verdict).read().strip()
        except Exception:
            mode = "ok"
        # The whole point: a refusal is HTTP 200 with ok:false, not an HTTP error.
        if mode == "refuse" and method == "sendMessage":
            out = {"ok": False, "error_code": 403,
                   "description": "Forbidden: bot was blocked by the user"}
        elif method == "sendMessage":
            out = {"ok": True, "result": {"message_id": 9001}}
        else:
            out = {"ok": True, "result": True}
        payload = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
srv = HTTPServer(("127.0.0.1", 0), H)
with open(portfile, "w") as f:
    f.write(str(srv.server_address[1]))
srv.serve_forever()
PYEOF
python3 "$TMP/stub.py" "$REQLOG" "$PORTFILE" "$VERDICT" &
STUB_PID=$!
for _ in $(seq 1 50); do [ -s "$PORTFILE" ] && break; sleep 0.1; done
PORT="$(cat "$PORTFILE" 2>/dev/null)"
if [ -z "$PORT" ]; then echo "FATAL: stub did not start"; exit 1; fi
export TELEGRAM_API_BASE="http://127.0.0.1:$PORT"

CHAT="10000000001"
SID="TESTSID"

make_state() { # dir
    local sd="$1"
    mkdir -p "$sd/progress"
    printf 'TELEGRAM_BOT_TOKEN=TESTTOKEN\n' > "$sd/.env"
    printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"AGENT_FINAL_ANSWER"}]}}' \
        > "$sd/transcript.jsonl"
    printf '[{"chat_id": "%s", "message_id": 555, "transcript_path": "%s"}]\n' \
        "$CHAT" "$sd/transcript.jsonl" > "$sd/progress/$SID.json"
    # Pre-trip the enforce guard so the Stop hook goes straight to the fallback
    # branch instead of spending a run on the nudge.
    : > "$sd/progress/enforce-$SID.marker"
}
state_exists() { [ -f "$1/progress/$SID.json" ] && echo yes || echo no; }
count() { local c; c="$(grep -c "^$1 " "$REQLOG" 2>/dev/null)"; echo "${c:-0}"; }
run_stop() { # dir
    printf '{"session_id":"%s","transcript_path":"%s","stop_hook_active":true}' \
        "$SID" "$1/transcript.jsonl" \
        | TELEGRAM_STATE_DIR="$1" python3 "$STOP_HOOK" >/dev/null
}

echo "telegram-fallback-lost-answer tests (FALLBACKVAK901)"
echo "==================================================="

# ---------------------------------------------------------------------------
echo ""
echo "(a) THE BUG: Telegram refuses with ok:false -> the answer must NOT be dropped"
SD_A="$TMP/a"; make_state "$SD_A"; : > "$REQLOG"; echo refuse > "$VERDICT"
assert_eq "state present before the fallback" "yes" "$(state_exists "$SD_A")"
run_stop "$SD_A"
assert_eq "the fallback did attempt a send" "1" "$(count sendMessage)"
assert_eq "refused send KEEPS the pending state for retry" "yes" "$(state_exists "$SD_A")"
assert_eq "refused send does NOT delete the placeholder" "0" "$(count deleteMessage)"
if grep -q "FAILED" "$SD_A/progress/debug.log" 2>/dev/null; then
    pass "the log says FAILED (it used to claim delivered)"
else
    fail "the log must record the failure"
fi

# ---------------------------------------------------------------------------
echo ""
echo "(b) NO REGRESSION: an accepted send still clears everything"
SD_B="$TMP/b"; make_state "$SD_B"; : > "$REQLOG"; echo ok > "$VERDICT"
run_stop "$SD_B"
assert_eq "accepted send delivered once" "1" "$(count sendMessage)"
assert_eq "accepted send removed the placeholder" "1" "$(count deleteMessage)"
assert_eq "accepted send cleared the pending state" "no" "$(state_exists "$SD_B")"

# ---------------------------------------------------------------------------
echo ""
echo "(c) BOUNDED: a permanently refusing chat gives up instead of piling up"
SD_C="$TMP/c"; make_state "$SD_C"; : > "$REQLOG"; echo refuse > "$VERDICT"
for i in 1 2 3 4; do run_stop "$SD_C"; done
assert_eq "still retrying at attempt 4" "yes" "$(state_exists "$SD_C")"
run_stop "$SD_C"   # 5th = MAX_FALLBACK_ATTEMPTS -> give up
assert_eq "gave up after the cap" "no" "$(state_exists "$SD_C")"
if grep -q "THE ANSWER IS LOST" "$SD_C/progress/debug.log" 2>/dev/null; then
    pass "giving up is logged loudly, not silently"
else
    fail "giving up must be logged loudly"
fi

# ---------------------------------------------------------------------------
echo ""
echo "(d) WATCHDOG: a refused orphan delivery keeps the state too"
SD_D="$TMP/d"; make_state "$SD_D"; : > "$REQLOG"; echo refuse > "$VERDICT"
rm -f "$SD_D/progress/enforce-$SID.marker"
# Age the placeholder past the wedged backstop (15 min) so the sweep fires.
python3 - "$SD_D/progress/$SID.json" <<'PYEOF'
import os, sys, time
old = time.time() - 20 * 60
os.utime(sys.argv[1], (old, old))
PYEOF
TELEGRAM_API_BASE="$TELEGRAM_API_BASE" python3 - "$WATCHDOG" "$SD_D/progress" <<'PYEOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("wd", sys.argv[1])
wd = importlib.util.module_from_spec(spec); spec.loader.exec_module(wd)
wd.handle_dir(sys.argv[2])
PYEOF
assert_eq "watchdog attempted the send" "1" "$(count sendMessage)"
assert_eq "watchdog KEEPS the state when the send was refused" "yes" "$(state_exists "$SD_D")"

# ---------------------------------------------------------------------------
echo ""
echo "==================================================="
TOTAL=$((PASS + FAIL))
echo "Results: $PASS/$TOTAL passed"
if [ "$FAIL" -gt 0 ]; then echo "FAILED: $FAIL tests"; exit 1; fi
echo "All tests passed."
