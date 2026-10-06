#!/usr/bin/env bash
# Switching the CIS flag on for an ALREADY INVOICED day, to match the paper.
#
# Measured 2026-10-04: invoice 10020 went out with CIS 20% on days #30-#33
# (owner, Telegram msg 830), while the four records kept cis=false. The next
# Palmer fitting day would have copied the false flag from them.
#
# Same narrowness as muhely-napidij-potlas.test.sh. What must stay refused:
#   - without --force
#   - together with another change (here: --job)
#   - when the issued invoice file carries NO CIS (record must not contradict paper)
# And an un-invoiced closed day takes --cis --force like any other edit.
set -u
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { echo "  ok   $1"; }
no() { echo "  FAIL $1"; fail=1; }

SANDBOX="$TMP/repo"
mkdir -p "$SANDBOX/scripts/muhely" "$SANDBOX/store/muhely/invoices"
cp scripts/muhely/jobs.py "$SANDBOX/scripts/muhely/"
cp store/muhely-business.json "$SANDBOX/store/" 2>/dev/null || true

setup() {  # $1 invoice_ref-or-null  $2 paper-cis: true|false|none
  python3 - "$1" "$2" "$SANDBOX/store" <<'PY'
import json, sys, time, os
from datetime import datetime
ref, paper, store = sys.argv[1], sys.argv[2], sys.argv[3]
def ts(h): return time.mktime(datetime(2026, 9, 29, h, 0).timetuple())
r = {"id": 1, "client": "Teszt", "job": "Furniture fitting", "started_at": ts(8),
     "stopped_at": ts(16), "billing": "daily", "cis": False, "rate": 200.0,
     "invoice_ref": None if ref == "null" else ref, "notes": []}
json.dump([r], open(os.path.join(store, "muhely-jobs.json"), "w"))
inv = os.path.join(store, "muhely", "invoices")
for f in os.listdir(inv):
    os.unlink(os.path.join(inv, f))
if paper != "none":
    c = paper == "true"
    json.dump({"cis": c, "lines": [{"desc": "x", "qty": 1, "unit": 200, "cis": c}]},
              open(os.path.join(inv, f"{ref}-teszt.json"), "w"))
PY
}
cis_of() { python3 -c "import json;print(json.load(open('$SANDBOX/store/muhely-jobs.json'))[0]['cis'])"; }
J="$SANDBOX/scripts/muhely/jobs.py"

setup 99001 true
python3 "$J" edit 1 --cis --force >/dev/null; rc=$?
[ $rc -eq 0 ] && [ "$(cis_of)" = True ] && ok "szamlazott nap, CIS-es papir, --force: bevezetve" || no "bevezetes (rc=$rc, cis=$(cis_of))"

setup 99001 true
python3 "$J" edit 1 --cis >/dev/null; rc=$?
[ $rc -ne 0 ] && [ "$(cis_of)" = False ] && ok "--force nelkul: elutasitva" || no "--force nelkul atment"

setup 99001 true
python3 "$J" edit 1 --cis --force --job "mas" >/dev/null; rc=$?
[ $rc -ne 0 ] && [ "$(cis_of)" = False ] && ok "mas valtozassal egyutt: elutasitva" || no "mas valtozassal atment"

setup 99001 false
python3 "$J" edit 1 --cis --force >/dev/null; rc=$?
[ $rc -ne 0 ] && [ "$(cis_of)" = False ] && ok "CIS nelkuli papir: elutasitva" || no "CIS nelkuli papirnal atment"

setup 99001 none
python3 "$J" edit 1 --cis --force >/dev/null; rc=$?
[ $rc -ne 0 ] && [ "$(cis_of)" = False ] && ok "hianyzo szamla-fajl: elutasitva" || no "hianyzo fajlnal atment"

setup null none
python3 "$J" edit 1 --cis --force >/dev/null; rc=$?
[ $rc -eq 0 ] && [ "$(cis_of)" = True ] && ok "szamlazatlan lezart nap, --force: bevezetve" || no "szamlazatlan nap (rc=$rc)"

[ $fail -eq 0 ] && echo "MIND OK"
exit $fail
