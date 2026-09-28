#!/usr/bin/env bash
# Filling in a missing day rate on an ALREADY INVOICED job.
#
# Measured 2026-09-10: four invoiced jobs (#3, #4, #5 on invoice 10012 and #11 on
# 10014) carry no `rate`, while the PDFs the customer holds show 200.00 on every
# line. The record said less than the paper.
#
# It was not fixable, because the tools formed a CLOSED LOOP: `jobs.py invoice`
# on such a job prints "add meg a dijat: jobs.py edit N --rate X", and that
# command refused itself on the invoiced guard -- with --force too. A tool must
# not advise a command it rejects.
#
# The exception is deliberately narrow, and the point of this test is the NARROWNESS
# as much as the fix. Four things must stay refused, or the guard is gone:
#   - overwriting a rate that IS set on an invoiced job (that contradicts the paper)
#   - filling a rate WITHOUT --force
#   - changing the description in the same call
#   - changing the description alone, as before
set -u
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { echo "  ok   $1"; }
no() { echo "  FAIL $1"; fail=1; }

# A COPY OF THE SCRIPT IN A COPY OF THE TREE, never the live ledger.
SANDBOX="$TMP/repo"
mkdir -p "$SANDBOX/scripts/muhely" "$SANDBOX/store"
cp scripts/muhely/jobs.py "$SANDBOX/scripts/muhely/"
cp store/muhely-business.json "$SANDBOX/store/" 2>/dev/null || true

led() {  # $1 rate-or-null  $2 invoice_ref-or-null
  python3 - "$1" "$2" "$SANDBOX/store/muhely-jobs.json" <<'PY'
import json, sys, time
from datetime import datetime
rate, ref, out = sys.argv[1], sys.argv[2], sys.argv[3]
def ts(h): return time.mktime(datetime(2026, 9, 3, h, 0).timetuple())
r = {"id": 1, "client": "Teszt", "job": "eredeti leiras", "started_at": ts(7),
     "stopped_at": ts(16), "billing": "daily", "cis": False,
     "rate": None if rate == "null" else float(rate),
     "invoice_ref": None if ref == "null" else ref}
json.dump([r], open(out, "w"))
PY
}
field() { python3 - "$1" "$SANDBOX/store/muhely-jobs.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[2]))[0].get(sys.argv[1]))
PY
}
notes() { python3 - "$SANDBOX/store/muhely-jobs.json" <<'PY'
import json, sys
print(len(json.load(open(sys.argv[1]))[0].get("notes") or []))
PY
}
run() { python3 "$SANDBOX/scripts/muhely/jobs.py" "$@" >"$TMP/out" 2>&1; echo $?; }

echo "muhely-napidij-potlas"

# 1. THE FIX: an invoiced job with no rate accepts the fill, and notes it.
led null 10012
rc=$(run edit 1 --rate 200 --force)
[ "$rc" = "0" ] && [ "$(field rate)" = "200.0" ] && [ "$(notes)" = "1" ] \
  && ok "hianyzo napidij potolva szamlazott munkan, jegyzettel" \
  || no "potlas: exit $rc, rate '$(field rate)', jegyzet '$(notes)'"

# 2. THE NARROWNESS: a rate that IS set on an invoiced job stays refused. This is
#    the case that would contradict the paper the customer holds.
led 200 10012
rc=$(run edit 1 --rate 250 --force)
[ "$rc" = "1" ] && [ "$(field rate)" = "200.0" ] \
  && ok "meglevo napidij felulirasa szamlazott munkan: elutasit" \
  || no "meglevo napidij: exit $rc, rate '$(field rate)'"

# 3. Without --force the fill is refused too: the flag is the deliberate step.
led null 10012
rc=$(run edit 1 --rate 200)
[ "$rc" = "1" ] && [ "$(field rate)" = "None" ] \
  && ok "--force nelkul: elutasit" \
  || no "--force nelkul: exit $rc, rate '$(field rate)'"

# 4. A description change riding along in the same call is refused whole: the
#    exception is for the rate alone, and must not become a way in.
led null 10012
rc=$(run edit 1 --rate 200 --job "atirt leiras" --force)
[ "$rc" = "1" ] && [ "$(field job)" = "eredeti leiras" ] && [ "$(field rate)" = "None" ] \
  && ok "leiras a potlassal egyutt: elutasit, semmi nem valtozik" \
  || no "egyutt: exit $rc, job '$(field job)', rate '$(field rate)'"

# 5. NEGATIVE CONTROL for the guard as a whole: the original refusal still stands
#    on its own ground, otherwise this test would pass by breaking everything.
led 200 10012
rc=$(run edit 1 --job "atirt leiras" --force)
[ "$rc" = "1" ] && [ "$(field job)" = "eredeti leiras" ] \
  && ok "leiras atirasa szamlazott munkan: valtozatlanul elutasit" \
  || no "leiras: exit $rc, job '$(field job)'"

# 6. NEGATIVE CONTROL the other way: on a NOT invoiced job --force still edits the
#    description, so the guard was never about --force itself.
led 200 null
rc=$(run edit 1 --job "uj leiras" --force)
[ "$rc" = "0" ] && [ "$(field job)" = "uj leiras" ] \
  && ok "nem szamlazott munka: a leiras atirhato" \
  || no "nem szamlazott: exit $rc, job '$(field job)'"

exit $fail
