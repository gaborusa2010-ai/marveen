#!/usr/bin/env bash
# The one promise the owner asked for: tell me before the auction closes.
#
# Jobalots warns FIFTEEN minutes ahead. The half-hourly round cannot keep that
# promise -- measured 2026-08-28, two relays eleven minutes late leaving four
# minutes to act, and in the worst case the mail is seen twenty-nine minutes
# later, after the auction is over. This watcher asks one narrow question so it
# can run every few minutes instead.
#
# The test runs OFFLINE against a fake Gmail. A test that needs the network is a
# test nobody runs, and worse, one that passes for the wrong reason on a bad day.
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

echo "auction-closing-watch"

# A stand-in for scripts/muhely/google_auth.py, earlier on the path. Two mails:
# one three minutes old (still actionable) and one forty minutes old (the
# auction closed long ago).
mkdir -p "$TMP/fake"
cat > "$TMP/fake/google_auth.py" <<'PY'
import time
NOW = int(time.time())
MAILS = {
    "friss": {"age_min": 3,  "subject": "Reminder: Participated auction ending soon!"},
    "regi":  {"age_min": 40, "subject": "Reminder: Participated auction ending soon!"},
}
def call(url, params=None, **kw):
    if url.endswith("/messages"):
        return {"messages": [{"id": k} for k in MAILS]}
    mid = url.rsplit("/", 1)[-1]
    m = MAILS[mid]
    return {"internalDate": str((NOW - m["age_min"] * 60) * 1000),
            "payload": {"headers": [{"name": "Subject", "value": m["subject"]}]}}
PY

run() { PYTHONPATH="$TMP/fake" HOME="$HOME" python3 scripts/auction-closing-watch.py "$@" 2>"$TMP/err"; }

# The module inserts scripts/muhely at the FRONT of sys.path, so the fake has to
# win over the real one. PYTHONPATH is not enough by itself; point the state file
# at the temp dir too, via a copy of the script with STATE overridden.
python3 - "$TMP" <<'PY'
import re, sys
tmp = sys.argv[1]
src = open("scripts/auction-closing-watch.py", encoding="utf-8").read()
src = src.replace('STATE = os.path.join(REPO, "store", "auction-closing-seen.json")',
                  f'STATE = {tmp + "/seen.json"!r}')
src = src.replace('MUHELY = os.path.join(REPO, "scripts", "muhely")',
                  f'MUHELY = {tmp + "/fake"!r}')
open(tmp + "/watch.py", "w", encoding="utf-8").write(src)
PY

OUT=$(python3 "$TMP/watch.py" 2>"$TMP/err")

echo "$OUT" | grep -q '"id": "friss"' && ok "megtalalja a friss zarulo ertesitot" \
                                      || no "a friss ertesito hianyzik"
echo "$OUT" | python3 -c "
import sys, json
rows = [json.loads(l) for l in sys.stdin if l.strip()]
f = next((r for r in rows if r.get('id') == 'friss'), None)
sys.exit(0 if f and f['still_worth_sending'] and f['age_min'] < 5 else 1)
" && ok "a friss meg kuldheto, es a kora percben megvan" \
  || no "a friss kora vagy a kuldhetoseg rossz"

echo "$OUT" | python3 -c "
import sys, json
rows = [json.loads(l) for l in sys.stdin if l.strip()]
r = next((x for x in rows if x.get('id') == 'regi'), None)
sys.exit(0 if r and not r['still_worth_sending'] else 1)
" && ok "a 40 perces mar NEM kuldheto -- az arveres lezart" \
  || no "a lejart ertesitot kuldhetonek mondta"

# Reporting is not remembering. Same split as the outbid buffer, and for the
# same reason: the decision to send comes after the listing, so the listing must
# not mark anything as handled.
grep -q "NEM jegyeztem meg" "$TMP/err" && ok "--commit nelkul nem jegyzi meg oket" \
                                       || no "hallgatolagosan megjegyezte oket"
[ ! -f "$TMP/seen.json" ] && ok "--commit nelkul allapotfajl sem keletkezik" \
                          || no "irt allapotot, pedig nem kertem"

python3 "$TMP/watch.py" --commit >/dev/null 2>&1
[ -f "$TMP/seen.json" ] && ok "--commit utan megvan az allapotfajl" \
                        || no "a --commit nem irt allapotot"

OUT2=$(python3 "$TMP/watch.py" 2>/dev/null)
echo "$OUT2" | grep -q '"new": 0' && ok "masodszorra mar nem jelenti ujra ugyanazt" \
                                  || no "ismetli a mar jelentett ertesitot"

# The state file must be its OWN. Sharing the half-hourly round's file would let
# the fast watcher mark a mail seen and the slow one never look at it.
grep -q "auction-closing-seen.json" scripts/auction-closing-watch.py \
  && ok "sajat allapotfajlt hasznal, nem a felorankent koret" \
  || no "a ket figyelo allapota osszekeveredett"

[ $fail -eq 0 ] && echo "  MIND RENDBEN" || echo "  BUKOTT"
exit $fail
