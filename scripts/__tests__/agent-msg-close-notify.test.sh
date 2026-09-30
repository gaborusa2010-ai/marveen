#!/usr/bin/env bash
# --close: carries `notify: false`, and can the test also say the opposite?
#
# What it guards: 2026-09-17, Roxy measured her own inbox and found 46% of the day's
# incoming (6/13) were [Eredmény] receipts that each pointed at a separate substantive
# message she had already received minutes earlier ("Reszletek: msg N"). Marveen measured
# the sending side: FIFTEEN such receipts in one day, all from this one code path.
#
# The reason it is a code fix and not a discipline fix: `--close` SENDS the content and
# CLOSES the row in the SAME call, so it is structurally always the "already answered
# separately" case the shared CLAUDE.md names. There is no legitimate use of this flag
# where the receipt is the only signal.
#
# HOW IT MEASURES WITHOUT A SERVER: a stub `curl` earlier on PATH records every -d payload
# to a file, and answers the POST with a valid id so the helper proceeds to the close.
# So this test posts NOTHING anywhere; it reads what the helper WOULD have sent.
set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$BASE/scripts/agent-msg.sh"
fails=0
TMP="$(mktemp -d -t agentmsg-notify.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

# Fixture token: the helper refuses to run without a readable token file, and its default
# (<repo>/store/.dashboard-token) does not exist on a fresh clone or a worktree -- there
# every case died on "no token file" and the negative control passed VACUOUSLY (no payload
# at all also has no notify:false). Exported, so the mutant copy below, whose own <repo>
# is $TMP/.., reads the same fixture instead of a path that is never there.
printf 'fixture-token\n' > "$TMP/token"
export MARVEEN_TOKEN_FILE="$TMP/token"
export MARVEEN_HOMOGLYPH_BIN="$BASE/scripts/lib/homoglyph.py"
unset MARVEEN_API_BASE MARVEEN_WEB_PORT

# --- the stub curl: log the payloads, fake a 200 + id for the POST, 200 for the PUT ---
mkdir -p "$TMP/bin"
cat > "$TMP/bin/curl" <<'STUB'
#!/usr/bin/env bash
# Record every -d / --data-binary payload, then answer like the dashboard API would.
payload=""; is_put=0
prev=""
for a in "$@"; do
  case "$prev" in -d|--data-binary) payload="$a" ;; esac
  case "$a" in PUT) is_put=1 ;; esac
  prev="$a"
done
printf '%s\n' "$payload" >> "$CURL_LOG"
if [ "$is_put" = 1 ]; then printf '{"ok":true}\n200'; else printf '{"ok":true,"id":9999}\n200'; fi
STUB
chmod +x "$TMP/bin/curl"

run_helper() {
  CURL_LOG="$TMP/log.txt" PATH="$TMP/bin:$PATH" \
    bash "$S" --close 1234 --result "$1" marveen roxy "proba szoveg" 2>&1
}

# ---- 1. POSITIVE: the close payload must carry notify:false ----------------------
: > "$TMP/log.txt"
out="$(run_helper 'kesz, reszletek: msg {id}')"
close_payload="$(grep -c '"notify": *false' "$TMP/log.txt" || true)"
if printf '%s' "$out" | grep -q "CLOSED 1234" && [ "$close_payload" -ge 1 ]; then
  echo "OK   a --close payloadja notify:false-t visz"
else
  echo "FAIL a --close payloadjabol HIANYZIK a notify:false"
  echo "     kimenet: $(printf '%s' "$out" | tr '\n' ' ' | head -c 120)"
  echo "     payloadok:"; sed 's/^/       /' "$TMP/log.txt"
  fails=$((fails+1))
fi

# ---- 2. NEGATIVE CONTROL: the test can see its ABSENCE -------------------------
# Same run against a copy whose notify flag was removed. If this still passes, the
# assertion above proves nothing.
MUT="$TMP/agent-msg-mutant.sh"
sed 's/, "notify": False//' "$S" > "$MUT"
if ! grep -q '"notify": False' "$MUT"; then
  : > "$TMP/log.txt"
  CURL_LOG="$TMP/log.txt" PATH="$TMP/bin:$PATH" \
    bash "$MUT" --close 1234 --result 'kesz, reszletek: msg {id}' marveen roxy "proba" >/dev/null 2>&1
  if ! grep -q '"status"' "$TMP/log.txt"; then
    # No close payload at all is not a red control, it is no control: an absent
    # payload has no notify:false either. The mutant must have REACHED the close.
    echo "FAIL a negativ kontroll URES -- a mutans el sem jutott a lezarasig"
    sed 's/^/       /' "$TMP/log.txt"
    fails=$((fails+1))
  elif grep -q '"notify": *false' "$TMP/log.txt"; then
    echo "FAIL a negativ kontroll ATMENT -- a teszt nem tud nemet mondani"
    fails=$((fails+1))
  else
    echo "OK   a negativ kontroll PIROS: a mutans payloadjaban nincs notify:false"
  fi
else
  echo "FAIL a mutans elkeszitese nem sikerult (a sed nem talalta a flaget)"
  fails=$((fails+1))
fi

# ---- 3. the RESULT text still gets the measured id, not a prediction -------------
: > "$TMP/log.txt"
run_helper 'kesz, reszletek: msg {id}' >/dev/null
if grep -q '"result": "kesz, reszletek: msg 9999"' "$TMP/log.txt"; then
  echo "OK   a {id} helyere a MERT id kerult (9999), a notify melett is"
else
  echo "FAIL a {id} csere elromlott"; sed 's/^/       /' "$TMP/log.txt"; fails=$((fails+1))
fi

# ---- 4. a close-nelkuli kuldes NEM kap notify mezot (nincs mit csillapitani) -----
: > "$TMP/log.txt"
CURL_LOG="$TMP/log.txt" PATH="$TMP/bin:$PATH" bash "$S" marveen roxy "csak kuldes" >/dev/null 2>&1
if ! grep -q 'notify' "$TMP/log.txt"; then
  echo "OK   sima kuldesben nincs notify mezo"
else
  echo "FAIL a sima kuldes payloadjaba bekerult a notify"; sed 's/^/       /' "$TMP/log.txt"; fails=$((fails+1))
fi

echo "---"
if [ "$fails" = 0 ]; then echo "MIND ZOLD"; exit 0; else echo "$fails BUKAS"; exit 1; fi
