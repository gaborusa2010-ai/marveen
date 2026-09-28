#!/usr/bin/env bash
# An issued invoice has to reach the WORK LOG, not only the invoice register.
#
# Measured 2026-09-20. Two files answer "is this day billed":
#   store/muhely-invoice-log.json   the register  -- learned it at issue time
#   store/muhely-jobs.json          invoice_ref   -- did NOT
# `jobs.py list` shows the second one, and the Sunday invoicing sentry reads
# `jobs.py list`. So the five days that went out on invoice 10016 still read
# `kesz` afterwards, and the next Sunday would have offered them up again.
#
# It had not bitten yet only because a MULTI-DAY invoice is built from a
# hand-written job file, which named no days at all -- so nothing was written
# back and nothing ever looked wrong. That is the shape this test guards.
#
# Cases 3 and 4 are the point. A test that only proves the happy path would
# pass just as well if the code wrote the number onto every job in the ledger.
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

LIVE_JOBS=$(md5sum store/muhely-jobs.json | cut -d' ' -f1)
LIVE_CNT=$(md5sum store/muhely-counters.json | cut -d' ' -f1)

S="$TMP/repo"
mkdir -p "$S/scripts/muhely" "$S/store"
cp scripts/muhely/*.py "$S/scripts/muhely/"
cp store/muhely-business.json "$S/store/"
# Never the live counter: this test spends invoice numbers.
python3 - "$S/store" <<'PY'
import json, sys
from datetime import date
json.dump({f"invoice_{date.today().year}": 5000},
          open(sys.argv[1] + "/muhely-counters.json", "w"))
PY

# Four closed days and one still running.
python3 - "$S/store" <<'PY'
import json, sys, time
base = time.time() - 9 * 86400
rows = []
for i in range(1, 5):
    st = base + i * 86400
    rows.append({"id": i, "client": "Probe Ltd", "job": f"Nap {i}", "address": "",
                 "started_at": st, "stopped_at": st + 9 * 3600, "rate": 200.0,
                 "cis": False, "billing": "daily", "days": None, "photos": [],
                 "notes": [], "invoice_ref": None, "no_charge": None})
rows.append({"id": 5, "client": "Probe Ltd", "job": "Meg fut", "address": "",
             "started_at": time.time() - 3600, "stopped_at": None, "rate": 200.0,
             "cis": False, "billing": "daily", "days": None, "photos": [],
             "notes": [], "invoice_ref": None, "no_charge": None})
json.dump(rows, open(sys.argv[1] + "/muhely-jobs.json", "w"), indent=2)
PY

# The Drive upload and the desktop copy are someone else's test. Off here, or a
# sandbox PDF would land in the owner's real folders.
export MUHELY_NO_DRIVE=1 MUHELY_NO_DESKTOP=1

jobfile() {  # $1 = target path, $2 = the linking key line (may be empty)
  cat > "$1" <<JSON
{
  "client": {"name": "Probe Ltd", "address": ["1 Test Rd"]},
  "job": "Day work",
  "cis": false,
  "lines": [
    {"desc": "Nap 1", "qty": 1, "unit": 200.00, "cis": false},
    {"desc": "Nap 2", "qty": 1, "unit": 200.00, "cis": false}
  ]$2
}
JSON
}

ref_of() {  # $1 = job id -> the invoice_ref on that day, or the word "null"
  python3 - "$S/store/muhely-jobs.json" "$1" <<'PY'
import json, sys
rows = json.load(open(sys.argv[1]))
r = next((x for x in rows if x["id"] == int(sys.argv[2])), None)
print(r.get("invoice_ref") if r and r.get("invoice_ref") else "null")
PY
}

echo "muhely-szamla-nap-visszavezetes"

echo "== 1. tobbnapos szamla: _job_ids -> mindegyik napra bekerul"
jobfile "$S/j1.json" ', "_job_ids": [1, 2]'
OUT=$( cd "$S" && python3 scripts/muhely/invoice.py invoice j1.json --out store/sz1.pdf 2>&1 )
R1=$(ref_of 1); R2=$(ref_of 2); R3=$(ref_of 3)
[ "$R1" = "5001" ] && [ "$R2" = "5001" ] && ok "a #1 es a #2 megkapta az 5001-et" \
                                         || no "a #1=$R1 a #2=$R2 (vart: 5001)"
[ "$R3" = "null" ] && ok "a meg nem szamlazott #3 erintetlen" \
                   || no "a #3 is megjelolve lett: $R3"
echo "$OUT" | grep -q "Munkanaplo" && ok "kiirta, mit jelolt meg" || no "nema maradt"

echo "== 2. a ketszer-szamlazas or MOST a MASODIK napot is latja"
# The old guard read `_job_id` only, so a second invoice naming day 2 -- the
# day that was NOT first in the original file -- went straight through. This is
# the case that pays for the multi-day support: the register knows, and the
# guard now asks it about every day, not only the first.
jobfile "$S/j-ism.json" ', "_job_ids": [2]'
OUT2=$( cd "$S" && python3 scripts/muhely/invoice.py invoice j-ism.json --out store/sz-ism.pdf 2>&1 ); RC2=$?
[ $RC2 -ne 0 ] && ok "a #2 ujraszamlazasa megall (exit $RC2)" \
               || no "a #2 ujraszamlazasat atengedte"
echo "$OUT2" | grep -q "5001" && ok "es megnevezi az 5001-et" || no "nem mondta meg a szamot"
[ -f "$S/store/sz-ism.pdf" ] && no "PDF-et is gyartott" || ok "es nem koltott sorszamot"

echo "== 3. NEGATIV KONTROLL: kotes nelkuli szamla nem jelol meg semmit"
jobfile "$S/j2.json" ''
OUT3=$( cd "$S" && python3 scripts/muhely/invoice.py invoice j2.json --out store/sz2.pdf 2>&1 )
R3b=$(ref_of 3); R4b=$(ref_of 4)
[ "$R3b" = "null" ] && [ "$R4b" = "null" ] && ok "egyetlen napra sem irt" \
                                           || no "megjelolt valamit: #3=$R3b #4=$R4b"
echo "$OUT3" | grep -q "EGYETLEN munkanaphoz sincs kotve" && ok "de FIGYELMEZTETETT" \
                                                          || no "csendben hagyta"
echo "$OUT3" | grep -q "szamlazva" && ok "es megadta a javito parancsot" \
                                   || no "nem mondta meg, mit tegyek"

echo "== 4. jobs.py szamlazva: kezi bevezetes"
OUT5=$( cd "$S" && python3 scripts/muhely/jobs.py szamlazva 5099 3 4 2>&1 )
[ "$(ref_of 3)" = "5099" ] && [ "$(ref_of 4)" = "5099" ] && ok "a #3 es #4 megkapta az 5099-et" \
                                                         || no "nem vezette be"

echo "== 5. NEGATIV KONTROLL: mas szamot NEM ir felul"
# The #3 now carries 5099, entered BY HAND -- so the register knows nothing
# about it and the double-invoice guard above cannot fire. This is exactly the
# gap where a silent overwrite would happen, and it must not.
jobfile "$S/j3.json" ', "_job_ids": [3]'
OUT4=$( cd "$S" && python3 scripts/muhely/invoice.py invoice j3.json --out store/sz3.pdf 2>&1 )
[ "$(ref_of 3)" = "5099" ] && ok "a #3 megtartotta az 5099-et" \
                           || no "felulirta: $(ref_of 3)"
echo "$OUT4" | grep -q "NEM irtam at" && ok "es szolt rola" || no "csendben hagyta"

echo "== 6. NEGATIV KONTROLL: futo munkara nem"
OUT6=$( cd "$S" && python3 scripts/muhely/jobs.py szamlazva 5100 5 2>&1 ); RC6=$?
[ "$(ref_of 5)" = "null" ] && ok "a futo #5 erintetlen" || no "megjelolte a futo munkat"
[ $RC6 -ne 0 ] && ok "es nem nulla exittel" || no "sikert jelentett"

echo "== 7. az ELES fajlok erintetlenek"
[ "$LIVE_JOBS" = "$(md5sum store/muhely-jobs.json | cut -d' ' -f1)" ] \
  && ok "muhely-jobs.json valtozatlan" || no "AZ ELES MUNKANAPLO VALTOZOTT"
[ "$LIVE_CNT" = "$(md5sum store/muhely-counters.json | cut -d' ' -f1)" ] \
  && ok "muhely-counters.json valtozatlan" || no "AZ ELES SZAMLALO VALTOZOTT"

[ $fail -eq 0 ] && echo "  MIND RENDBEN" || echo "  VOLT HIBA"
exit $fail
