#!/usr/bin/env bash
# A job with no day rate must fail in the EXIT CODE, not only on the screen.
#
# Until 2026-09-09 `jobs.py invoice` printed "nincs munkadij ... a szamla sorok URESEK"
# and then returned 0. The sentence stops a person who reads Hungarian in a terminal;
# it stops nothing that checks $?. Measured that day on job #12 (2026-09-07, started
# before `start` learned to read the rate from muhely-business.json): file written,
# warning printed, exit 0, zero labour lines. The owner's money rides on this path.
#
# And the fix names a flag in its message, so the flag has to exist: `edit --rate` was
# added the same day for exactly that reason.
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

SANDBOX="$TMP/repo"
mkdir -p "$SANDBOX/scripts/muhely" "$SANDBOX/store"
cp scripts/muhely/jobs.py "$SANDBOX/scripts/muhely/"
cp store/muhely-business.json "$SANDBOX/store/" 2>/dev/null || true

led() {  # $1 = rate literal ("null" or a number)
  python3 - "$1" "$SANDBOX/store/muhely-jobs.json" <<'PY'
import json, sys, time
from datetime import datetime
rate, out = sys.argv[1], sys.argv[2]
ts = lambda h: time.mktime(datetime(2026, 9, 3, h, 0).timetuple())
json.dump([{"id": 1, "client": "Teszt", "job": "teszt", "started_at": ts(7),
            "stopped_at": ts(16), "rate": None if rate == "null" else float(rate),
            "billing": "daily", "cis": False}], open(out, "w"))
PY
}
inv() { python3 "$SANDBOX/scripts/muhely/jobs.py" invoice 1 --out "$TMP/o.json" >/dev/null 2>&1; echo $?; }
nlines() { python3 -c "import json,sys;print(len(json.load(open(sys.argv[1])).get('lines',[])))" "$TMP/o.json"; }

echo "muhely-nincs-napidij"

# 1. no rate -> non-zero exit, and no labour line
led null
rc=$(inv)
[ "$rc" != "0" ] && ok "nincs napidij -> exit $rc (nem 0)" || no "nincs napidij -> exit 0, ezt egy szkript nem latja"
[ "$(nlines)" = "0" ] && ok "nincs napidij -> nulla sor" || no "nincs napidij -> sort epitett"

# 2. a rate present -> clean exit and one line. Without this the check could pass
#    by failing on everything.
led 200
rc=$(inv)
[ "$rc" = "0" ] && ok "van napidij -> exit 0" || no "van napidij -> exit $rc"
[ "$(nlines)" = "1" ] && ok "van napidij -> egy sor" || no "van napidij -> $(nlines) sor"

# 3. the flag the error message names must exist
python3 "$SANDBOX/scripts/muhely/jobs.py" edit --help 2>&1 | grep -q -- "--rate" \
  && ok "az 'edit --rate' kapcsolo letezik, ahogy a hibauzenet igeri" \
  || no "a hibauzenet olyan kapcsolot nevez meg, ami nincs"

exit $fail
