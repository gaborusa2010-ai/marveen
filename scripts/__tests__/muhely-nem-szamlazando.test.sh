#!/usr/bin/env bash
# A job marked no_charge is not a job with a missing rate.
#
# Measured 2026-09-10 on the one real case, job #1 (2026-08-22, the favour the owner
# decided not to bill, rate 0.0, no_charge filled in): `jobs.py invoice 1` fired the
# missing-rate guard -- "nincs munkadij ... add meg a dijat: jobs.py edit 1 --rate
# <osszeg>", exit 3. That advice contradicts the record it had just read. The
# decision not to charge is IN the file; asking for a figure is asking to undo it.
#
# Margo raised it from the other side (via Marveen, msg 4206): the worklog-app treats
# rate 0 as a real zero fee ON PURPOSE, this file treats it as missing. Both readings
# stay. What was wrong is that no_charge, which is neither, fell through to the second.
#
# The last two cases are the point: the missing-rate guard must still fire where it
# should, or this "fix" would just be a hole with a comment on it.
set -u
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { echo "  ok   $1"; }
no() { echo "  FAIL $1"; fail=1; }

SANDBOX="$TMP/repo"
mkdir -p "$SANDBOX/scripts/muhely" "$SANDBOX/store"
cp scripts/muhely/jobs.py "$SANDBOX/scripts/muhely/"
cp store/muhely-business.json "$SANDBOX/store/" 2>/dev/null || true

led() {  # $1 rate-or-null  $2 no_charge-text-or-null
  python3 - "$1" "$2" "$SANDBOX/store/muhely-jobs.json" <<'PY'
import json, sys, time
from datetime import datetime
rate, nc, out = sys.argv[1], sys.argv[2], sys.argv[3]
def ts(h): return time.mktime(datetime(2026, 9, 3, h, 0).timetuple())
json.dump([{"id": 1, "client": "Teszt", "job": "teszt", "started_at": ts(7),
            "stopped_at": ts(16), "billing": "daily", "cis": False,
            "rate": None if rate == "null" else float(rate),
            "no_charge": None if nc == "null" else nc}], open(out, "w"))
PY
}
run() { rm -f "$TMP/o.json"
        (cd "$SANDBOX" && python3 scripts/muhely/jobs.py invoice 1 --out "$TMP/o.json" \
           >"$TMP/out" 2>"$TMP/err"); echo $?; }

echo "muhely-nem-szamlazando"

# 1. THE FIX: a no_charge job exits clean and writes NO invoice file.
led 0 "nem szamlazando (szivesseg)"
rc=$(run)
[ "$rc" = "0" ] && [ ! -f "$TMP/o.json" ] && grep -q "NEM SZAMLAZANDO" "$TMP/out" \
  && ok "no_charge: tiszta kilepes, nincs szamla-fajl" \
  || no "no_charge: exit $rc, fajl $([ -f "$TMP/o.json" ] && echo van || echo nincs)"

# 2. The reason is REPEATED BACK, not just the refusal. Whoever runs this needs to
#    see what was decided, or the next step is to go looking for it.
grep -q "szivesseg" "$TMP/out" \
  && ok "a dontes szovege is megjelenik" \
  || no "a dontes szovege nem jelent meg"

# 3. It must not depend on the rate being zero: no_charge with a rate set still
#    refuses, because the decision outranks the figure.
led 200 "nem szamlazando (szivesseg)"
rc=$(run)
[ "$rc" = "0" ] && [ ! -f "$TMP/o.json" ] \
  && ok "no_charge kitoltott dijjal is: nem szamlaz" \
  || no "no_charge + rate: exit $rc, fajl $([ -f "$TMP/o.json" ] && echo van || echo nincs)"

# 4. NEGATIVE CONTROL: a missing rate WITHOUT no_charge still fails loudly, exit 3.
#    This is the guard the fix steps in front of, and it has to survive.
led null null
rc=$(run)
# The warning goes to STDERR on purpose (jobs.py says so: "the failure has to be in
# the channel a caller can actually test"), so look for it there. The first version
# of this case grepped stdout, found nothing, and failed while the exit code was
# already right -- a reminder that "no match" and "wrong stream" look identical.
[ "$rc" = "3" ] && grep -q "nincs munkadij" "$TMP/err" \
  && ok "rate nelkul, no_charge nelkul: valtozatlanul exit 3, stderr-en" \
  || no "hianyzo rate: exit $rc (vart 3), stderr: $(head -c 60 "$TMP/err")"

# 5. NEGATIVE CONTROL: an ordinary job still produces its invoice file.
led 200 null
rc=$(run)
[ "$rc" = "0" ] && [ -f "$TMP/o.json" ] \
  && ok "rendes munka: szamla-fajl elkeszul" \
  || no "rendes munka: exit $rc, fajl $([ -f "$TMP/o.json" ] && echo van || echo nincs)"

exit $fail
