#!/bin/bash
# CIMFUGGELEK901 -- contract tests for scripts/eligazitas-kartyacim-fuggelek.py and
# for its wiring into agent-night-shift.sh.
#
# WHAT WENT WRONG (measured, so the test has something to hold on to)
# Card #238's title is "A gazda megbizasa: hangok gyujtese a kollekcioba --
# EGYELORE CSAK ANGOL ES MAGYAR". Three consecutive nights of hand-written
# briefings in store/night-briefing-anorak.txt.sent-* passed it on three
# different ways, each losing something different, one of them wrong outright
# ("EGYELORE felfuggesztve" -- the card is not suspended). Anorak caught it on
# the third night and guessed a fixed-width cut in the caller's tooling; that was
# refuted by measurement (the only generator cuts at [:90], the title is 81), and
# the real cause -- a nightly paraphrase -- is worse, because a fixed cut loses
# the same words every night and can be fixed once.
#
# Hermetic: a stub API on a local port and a stub agent-msg.sh. Never the live
# dashboard, never a real send.
set -u

PASS=0; FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  PASS: $1"; }
no()   { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
has()  { case "$2" in *"$3"*) ok "$1" ;; *) no "$1 (missing '$3')" ;; esac; }
hasnt(){ case "$2" in *"$3"*) no "$1 (unexpected '$3')" ;; *) ok "$1" ;; esac; }

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
PORT=""
API_PID=""
cleanup() { [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

# The helper reads its bearer token from <its own repo>/store/.dashboard-token.
# Running it from $REPO made the suite depend on the live store: on a checkout
# without that file (a fresh clone, a worktree) every lookup failed-open and 7
# cases went red for a reason unrelated to the helper; on the live install it
# sent the REAL dashboard token to the stub. So the helper runs as a byte copy
# from a throwaway tree that carries a fixture token.
HROOT="$TMP/helper-root"
mkdir -p "$HROOT/scripts" "$HROOT/store"
cp "$REPO/scripts/eligazitas-kartyacim-fuggelek.py" "$HROOT/scripts/"
printf 'fixture-token\n' > "$HROOT/store/.dashboard-token"
HELPER="$HROOT/scripts/eligazitas-kartyacim-fuggelek.py"

echo "eligazitas cim-fuggelek (CIMFUGGELEK901)"
echo "========================================"

# A stub board on a free port. The long title is the real one, verbatim.
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
cat > "$TMP/api.py" <<'PYEOF'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
CARDS = [
  {"seq": 238, "id": "428feb09", "status": "planned", "priority": "low", "assignee": "anorak",
   "title": "A gazda megbizasa: hangok gyujtese a kollekcioba -- EGYELORE CSAK ANGOL ES MAGYAR"},
  {"seq": 240, "id": "30ac65fa", "status": "planned", "priority": "normal", "assignee": "anorak",
   "title": "ALLO MEGKOTES: a gazda ele csak AZNAP visszamert tetel mehet"},
]
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/kanban":
            self.send_response(404); self.end_headers(); return
        # Only the fixture token opens the board, so a green run also proves the
        # helper authenticated with the token of the tree it runs from.
        if self.headers.get("Authorization") != "Bearer fixture-token":
            self.send_response(401); self.end_headers(); return
        b = json.dumps({"cards": CARDS}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PYEOF
python3 "$TMP/api.py" "$PORT" & API_PID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null -H "Authorization: Bearer fixture-token" "http://127.0.0.1:$PORT/api/kanban" && break
  python3 -c 'import time; time.sleep(0.2)'
done
export DASHBOARD_API="http://127.0.0.1:$PORT"
if curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer fixture-token" "http://127.0.0.1:$PORT/api/kanban" | grep -q 200; then
  ok "the stub board answers (otherwise nothing below proves anything)"
else
  no "the stub board answers (otherwise nothing below proves anything)"
fi

# ---------------------------------------------------------------------------
echo ""
echo "(1) The paraphrase stays, the verbatim title arrives beside it"
# ---------------------------------------------------------------------------
cat > "$TMP/brief.txt" <<'EOF'
Esti eligazitas.
  #238 428feb09  Hangok gyujtese, EGYELORE CSAK ANGOL. Alacsony prioritas.
  #240 30ac65fa  ALLO MEGKOTES.
EOF
OUT="$(python3 "$HELPER" "$TMP/brief.txt" 2>&1)"
has "the hand-written line is left alone" "$OUT" "Hangok gyujtese, EGYELORE CSAK ANGOL. Alacsony prioritas."
# The load-bearing assertion: the four words the paraphrase dropped.
has "the dropped constraint arrives verbatim" "$OUT" "EGYELORE CSAK ANGOL ES MAGYAR"
has "with the card's status and owner" "$OUT" "[planned / low / anorak]"
has "the second referenced card is there too" "$OUT" "ALLO MEGKOTES: a gazda ele csak AZNAP visszamert tetel mehet"

# ---------------------------------------------------------------------------
echo ""
echo "(2) Not applied twice, and silent when there is nothing to add"
# ---------------------------------------------------------------------------
printf '%s\n' "$OUT" > "$TMP/brief2.txt"
OUT2="$(python3 "$HELPER" "$TMP/brief2.txt" 2>&1)"
N1="$(printf '%s' "$OUT2" | grep -c 'A HIVATKOZOTT KARTYAK CIME SZO SZERINT')"
# A rejected send is retried from the same file next start; appending twice would
# read to the agent as two separate statements of the same fact.
if [ "$N1" = 1 ]; then ok "a second pass does not append the block again"; else no "a second pass does not append the block again (got $N1)"; fi
printf 'Semmi kartyaszam nincs ebben.\n' > "$TMP/nocard.txt"
OUT3="$(python3 "$HELPER" "$TMP/nocard.txt" 2>&1)"
hasnt "no card reference -> no appendix at all" "$OUT3" "A HIVATKOZOTT KARTYAK"
if [ "$OUT3" = "Semmi kartyaszam nincs ebben." ]; then ok "and the text comes back byte-identical"; else no "and the text comes back byte-identical"; fi

# ---------------------------------------------------------------------------
echo ""
echo "(3) A card named but not on the open board: honest, not alarming"
# ---------------------------------------------------------------------------
# Measured on the live board: #307 is a real, done card, archived that evening at
# 20:48. GET /api/kanban serves only non-archived rows, rejects every query
# parameter with 400, and GET /api/kanban/<id> is 404 -- so the API CANNOT tell an
# archived card from a non-existent one, and the line must not claim it can.
printf 'A #307-es valtozas ota mas a helyzet.\n' > "$TMP/gone.txt"
OUT4="$(python3 "$HELPER" "$TMP/gone.txt" 2>&1)"
has "it says the card is not among the OPEN cards" "$OUT4" "nem talalhato a NYITOTT kartyak kozott"
has "and names archived as a possibility" "$OUT4" "archivalt"
hasnt "it does NOT assert the card does not exist" "$OUT4" "NINCS ILYEN KARTYA"

# ---------------------------------------------------------------------------
echo ""
echo "(4) It can never stop a send -- the degradation path"
# ---------------------------------------------------------------------------
# The send path runs unattended at 23:00 and 06:00. An appendix is a nicety; the
# briefing arriving is not.
# NOT `VAR=x OUT=$(...)`: with an assignment on the left that is an assignment
# LIST, so it rewrites the exported variable for the rest of the file rather than
# scoping it to one command. The first draft of this test did exactly that and
# broke case (5) below -- the test poisoned its own environment, and the failure
# read as a defect in the wiring.
OUT5="$(env DASHBOARD_API=http://127.0.0.1:1 python3 "$HELPER" "$TMP/brief.txt" 2>/dev/null)"; RC5=$?
if [ "$RC5" = 0 ]; then ok "an unreachable board still exits 0"; else no "an unreachable board still exits 0 (got $RC5)"; fi
has "and the original briefing text is returned whole" "$OUT5" "Hangok gyujtese, EGYELORE CSAK ANGOL. Alacsony prioritas."
hasnt "with no appendix invented from nothing" "$OUT5" "A HIVATKOZOTT KARTYAK"

# ---------------------------------------------------------------------------
echo ""
echo "(5) Wired into agent-night-shift.sh, and the send survives a broken helper"
# ---------------------------------------------------------------------------
CASE="$TMP/inst"
mkdir -p "$CASE/scripts" "$CASE/store"
cp "$REPO/scripts/agent-night-shift.sh" "$CASE/scripts/"
# A stub send that records what it was actually handed on stdin.
cat > "$CASE/scripts/agent-msg.sh" <<'MEOF'
#!/bin/bash
cat > "$(dirname "$0")/../store/kikuldve.txt"
echo "OK id=999"
MEOF
chmod +x "$CASE/scripts/agent-msg.sh"
cp "$HELPER" "$CASE/scripts/"
printf 'Eligazitas.\n  #238 Hangok gyujtese, EGYELORE CSAK ANGOL.\n' > "$CASE/store/night-briefing-anorak.txt"
printf 'fixture-token\n' > "$CASE/store/.dashboard-token"

. "$CASE/scripts/agent-night-shift.sh"
LOG="$(send_night_briefing "$CASE" anorak 2>&1)"
SENT="$(cat "$CASE/store/kikuldve.txt" 2>/dev/null)"
has "the send happened" "$LOG" "eligazitas kikuldve"
has "and what went out carries the verbatim title" "$SENT" "EGYELORE CSAK ANGOL ES MAGYAR"
has "next to the hand-written line" "$SENT" "Hangok gyujtese, EGYELORE CSAK ANGOL."
if ls "$CASE/store/night-briefing-anorak.txt.sent-"* >/dev/null 2>&1; then
  ok "the briefing file is retired, as before"
else
  no "the briefing file is retired, as before"
fi
# Negative control: break the helper and the briefing must STILL go out. This is
# the assertion that matters at 23:00 with nobody awake.
rm -f "$CASE/store/kikuldve.txt"
printf 'Eligazitas ketto.\n  #238 rovid alak.\n' > "$CASE/store/night-briefing-anorak.txt"
printf 'import sys; sys.exit(9)\n' > "$CASE/scripts/eligazitas-kartyacim-fuggelek.py"
LOG2="$(send_night_briefing "$CASE" anorak 2>&1)"
SENT2="$(cat "$CASE/store/kikuldve.txt" 2>/dev/null)"
has "a broken helper does not stop the send" "$LOG2" "eligazitas kikuldve"
has "the original text still goes out" "$SENT2" "Eligazitas ketto."
has "and the skip is stated, not silent" "$LOG2" "cim-fuggelek kihagyva"

echo ""
echo "======================================="
echo "PASS: $PASS  FAIL: $FAIL"
[ "$FAIL" = 0 ] || exit 1
