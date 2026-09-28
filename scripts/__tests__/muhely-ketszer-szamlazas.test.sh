#!/usr/bin/env bash
# A second invoice for the same job must be a DECISION, not a side effect.
#
# Measured 2026-09-10 in a sandbox copy of the tree: job #11, already invoiced as
# 10014, rendered again without a word and moved the counter from 10015 to 10016.
# Two invoices for one day of work, different numbers, and the customer holding
# both would be right to ask which one to pay.
#
# The fact was already on file: `issued_for` has answered this since the register
# was added, and jobs.py `edit` has been asking it all along. It was never asked at
# the one moment that spends a number.
#
# What this test measures, and the last two are the point:
#   1. a second invoice for the same job stops, and spends NO number
#   2. --force lets it through, because a corrective invoice is a real thing
#   3. a FIRST invoice is untouched -- otherwise the guard would "pass" by
#      refusing everything
#   4. a QUOTE is untouched, on a job that already has an invoice: a customer may
#      be re-quoted any number of times, and a quote says nothing about what the
#      work WAS
set -u
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { echo "  ok   $1"; }
no() { echo "  FAIL $1"; fail=1; }

SANDBOX="$TMP/repo"
mkdir -p "$SANDBOX/scripts/muhely" "$SANDBOX/store"
cp scripts/muhely/*.py "$SANDBOX/scripts/muhely/"
cp store/muhely-business.json "$SANDBOX/store/" 2>/dev/null || true

# A COUNTER AND A REGISTER OF OUR OWN, never the live ones: this test spends
# invoice numbers, and spending a real one is not undoable.
python3 - "$SANDBOX/store" <<'PY'
import json, sys, time
from datetime import date
st = sys.argv[1]
# THE COUNTER KEY CARRIES THE YEAR: next_number builds it as f"{kind}_{year}", so a
# file written with a plain "invoice" key is silently ignored and the sandbox starts
# from 1. That happened on the first run of this test: every assertion still passed,
# because they compare before against after -- but the numbers were 1, 2, 3 instead
# of ten thousand, and the test was measuring a shape the live tree does not have.
y = date.today().year
json.dump({f"invoice_{y}": 10014, f"quote_{y}": 20003},
          open(st + "/muhely-counters.json", "w"))
json.dump([{"kind": "invoice", "reference": "10014", "job_id": 11,
            "client": "Teszt", "pdf": st + "/regi.pdf", "at": int(time.time())}],
          open(st + "/muhely-invoice-log.json", "w"))
PY

job() {  # $1 = _job_id
  python3 - "$1" "$TMP/job.json" <<'PY'
import json, sys
json.dump({"_job_id": int(sys.argv[1]),
           "client": {"name": "Teszt Kft"},
           "lines": [{"desc": "Day work", "qty": 1, "unit": 200.0, "cis": False}]},
          open(sys.argv[2], "w"))
PY
}
counter() { (cd "$SANDBOX" && python3 scripts/muhely/invoice.py next-number invoice); }
run() { (cd "$SANDBOX" && python3 scripts/muhely/invoice.py "$@" >"$TMP/out" 2>"$TMP/err"); echo $?; }

echo "muhely-ketszer-szamlazas"

# 1. THE GUARD: a job that already has an invoice stops, and no number is spent.
job 11
before=$(counter)
rc=$(run invoice "$TMP/job.json" --out "$TMP/masodik.pdf")
after=$(counter)
[ "$rc" = "3" ] && [ "$before" = "$after" ] && [ ! -f "$TMP/masodik.pdf" ] \
  && grep -q "MAR MENT SZAMLA" "$TMP/err" \
  && ok "masodik szamla ugyanarra a munkara: megall, sorszam nem fogy ($before)" \
  || no "masodik szamla: exit $rc, szamlalo $before -> $after, PDF $([ -f "$TMP/masodik.pdf" ] && echo van || echo nincs)"

# 2. --force IS the decision, and then it goes through and spends a number.
job 11
before=$(counter)
rc=$(run invoice "$TMP/job.json" --out "$TMP/helyesbito.pdf" --force)
after=$(counter)
[ "$rc" = "0" ] && [ "$before" != "$after" ] && [ -f "$TMP/helyesbito.pdf" ] \
  && ok "--force: atmegy, uj sorszam ($before -> $after)" \
  || no "--force: exit $rc, szamlalo $before -> $after"

# 3. NEGATIVE CONTROL: a job with NO prior invoice is untouched.
job 99
before=$(counter)
rc=$(run invoice "$TMP/job.json" --out "$TMP/elso.pdf")
after=$(counter)
[ "$rc" = "0" ] && [ -f "$TMP/elso.pdf" ] && [ "$before" != "$after" ] \
  && ok "elso szamla: valtozatlanul atmegy ($before -> $after)" \
  || no "elso szamla: exit $rc, szamlalo $before -> $after"

# 4. NEGATIVE CONTROL: a QUOTE on the already-invoiced job is untouched.
job 11
rc=$(run quote "$TMP/job.json" --out "$TMP/arajanlat.pdf")
[ "$rc" = "0" ] && [ -f "$TMP/arajanlat.pdf" ] \
  && ok "arajanlat ugyanarra a munkara: valtozatlanul atmegy" \
  || no "arajanlat: exit $rc"

exit $fail
