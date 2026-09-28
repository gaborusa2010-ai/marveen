#!/usr/bin/env bash
# The ledger's notes are INTERNAL. They must not reach the customer's invoice.
#
# invoice.py prints job["notes"] on the PDF under a "Notes" heading
# (invoice.py:358-361) -- a real feature, for a real customer note like
# "Lead time 3 weeks from deposit." But the notes jobs.py collects are provenance
# ("a befejezes ideje a gazda szava, Telegram ..."), corrections ("napidij: nincs
# -> 200.0") and open questions, in Hungarian, on an English invoice.
#
# Measured 2026-09-09: job #13 carried 277 characters of exactly that, and would
# have gone out on the Palmer invoice the first time this path was used. The two
# invoices already issued escaped only because they were built from hand-written
# job files that had no notes at all -- so nothing had ever tested this.
#
# The guarantee: cmd_invoice writes the ledger notes to `_notes`, which invoice.py
# does not read. A customer note stays possible, by putting `notes` into the job
# file deliberately.
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

python3 - "$SANDBOX/store/muhely-jobs.json" <<'PY'
import json, sys, time
from datetime import datetime
ts = lambda h: time.mktime(datetime(2026, 9, 3, h, 0).timetuple())
json.dump([{"id": 1, "client": "Teszt", "job": "teszt", "started_at": ts(7),
            "stopped_at": ts(16), "rate": 200.0, "billing": "daily", "cis": False,
            "notes": [{"at": ts(16), "text": "BELSO: a befejezes a gazda szava, Telegram."},
                      {"at": ts(16), "text": "BELSO: napidij: nincs -> 200.0"}]}],
          open(sys.argv[1], "w"))
PY

python3 "$SANDBOX/scripts/muhely/jobs.py" invoice 1 --out "$TMP/o.json" >/dev/null 2>&1
rc=$?
[ "$rc" = "0" ] && ok "a szamla-bemenet elkeszult (exit 0)" || no "invoice exit $rc"

echo "muhely-belso-jegyzet-nem-a-vevoe"

# 1. the customer-facing field must be absent or empty
v=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('notes') or '')" "$TMP/o.json")
[ -z "$v" ] && ok "a vevonek szant 'notes' mezo URES" || no "a 'notes' mezobe belso szoveg kerult: ${v:0:60}"

# 2. and the internal text must not be lost either -- it is needed for the record
i=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('_notes') or '')" "$TMP/o.json")
case "$i" in
  *"BELSO: napidij"*) ok "a belso jegyzet megvan a '_notes' mezoben" ;;
  *) no "a belso jegyzet ELVESZETT: '${i:0:60}'" ;;
esac

# 3. NEGATIVE CONTROL: a deliberate customer note still reaches the PDF field.
#    Without this the check could pass by simply dropping everything.
python3 - "$TMP/o.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["notes"] = "Lead time 3 weeks from deposit."
json.dump(d, open(p, "w"))
PY
v=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('notes') or '')" "$TMP/o.json")
[ "$v" = "Lead time 3 weeks from deposit." ] \
  && ok "a szandekos vevo-jegyzet tovabbra is beirhato" \
  || no "a szandekos vevo-jegyzet nem all meg a mezoben"

exit $fail
