#!/usr/bin/env bash
# All THREE billing modes have to survive `jobs.py invoice`, not just the two the
# owner uses today.
#
# Measured 2026-09-18 (Cody found it, Forgacs reproduced and fixed it): the
# `hourly` branch crashed with UnboundLocalError, because `days` was only ever
# set inside the `fixed` and `daily` branches while the closing print runs after
# all three. `daily` and `fixed` exited 0, so the main path looked healthy and
# the fault sat there unnoticed.
#
# WHY A TEST AND NOT JUST THE ONE-LINE FIX: the crash landed AFTER the job file
# was already written, and written correctly. Whoever looks at the FILE sees good
# input and carries on to invoice.py; whoever looks at `$?` stops. A failure that
# leaves a usable artefact behind is the kind that gets re-introduced, because
# the next person to touch this function sees a working output.
#
# The fix is a default `days` ABOVE the branches, so a fourth billing mode cannot
# forget it. That is what case 1 guards; case 4 guards the number itself, because
# a default that silently replaced the exact quotient would also be a bug.
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

# Run from a COPY. jobs.py derives its store path from its own file location, so a
# copy writes its own ledger and store/muhely-jobs.json is never touched. Running
# it from the repo would write the LIVE ledger -- this is the owner's money.
mkdir -p "$TMP/scripts/muhely" "$TMP/store"
cp scripts/muhely/jobs.py "$TMP/scripts/muhely/" || exit 1
LIVE_BEFORE=$(md5sum store/muhely-jobs.json 2>/dev/null | cut -d' ' -f1)

# $1 = billing mode, $2 = rate, $3 = start, $4 = stop. Leaves the run's stdout in
# $OUT and the job file at $TMP/job1.json.
run() {
  ( cd "$TMP" && rm -f store/muhely-jobs.json job1.json
    python3 scripts/muhely/jobs.py start --client Probe --job T \
        --rate "$2" --billing "$1" --at "$3" >/dev/null 2>&1
    python3 scripts/muhely/jobs.py stop --at "$4" >/dev/null 2>&1 ) || return 1
  OUT=$(cd "$TMP" && python3 scripts/muhely/jobs.py invoice 1 2>&1); RC=$?
  return 0
}

echo "muhely-jobs-invoice-agak"

# 1. Every mode reaches the end. RC is read from the python run itself, never
# through a pipe: a tail or a grep would hand back ITS OWN exit code and the
# crash would read as a pass.
for m in "hourly 25" "daily 200" "fixed 1500"; do
  set -- $m
  if run "$1" "$2" 08:00 16:00; then
    [ "$RC" -eq 0 ] && ok "$1 -> exit 0" \
                    || no "$1 -> exit $RC: $(printf '%s' "$OUT" | tail -1)"
  else
    no "$1 -> a start/stop elohivas elhalt"
  fi
done

# 2. NEGATIV KONTROLL a merohoz magahoz. If $RC could never be non-zero, case 1
# is decoration. An unknown job id must fail.
OUT=$(cd "$TMP" && python3 scripts/muhely/jobs.py invoice 99 2>&1); RC=$?
[ "$RC" -ne 0 ] && ok "negativ kontroll: ismeretlen job -> nem nulla exit" \
                || no "negativ kontroll: ismeretlen job MEGIS nulla exit, az 1. eset ertektelen"

# 3. The job file is written on every mode, so a green exit is not hiding an
# empty hand.
run hourly 25 08:00 16:00
[ -s "$TMP/job1.json" ] && ok "hourly -> a job fajl megszuletett" \
                        || no "hourly -> nincs job fajl"

# 4. The closing line the owner reads in the workshop must carry the SAME number
# as the invoice line. 9 hours is 1.125 days, and the exact quotient is the point:
# the rounded 1.12 would cost a whole pound through qty * unit in invoice.py.
run hourly 200 07:00 16:00
if printf '%s' "$OUT" | grep -q "1.125 nap"; then
  python3 - "$TMP/job1.json" <<'PY' && ok "hourly tort nap: a zaro sor es a qty ugyanaz a szam" \
                                    || no "hourly tort nap: a qty NEM 1.125"
import json, sys
sys.exit(0 if json.load(open(sys.argv[1]))["lines"][0]["qty"] == 1.125 else 1)
PY
else
  no "hourly tort nap: a zaro sor nem 1.125 nap"
fi

# 5. "1 days" went out on a customer PDF until 2026-09-18. A fraction stays
# plural, so this is not a "less than two" test.
run hourly 25 08:00 16:00
printf '%s' "$(python3 -c "
import json; print(json.load(open('$TMP/job1.json'))['lines'][0]['desc'])")" \
  | grep -q "(1 day)" && ok "egesz nap -> 'day', nem 'days'" \
                      || no "egesz nap -> a leirasban nem '1 day' all"

# 6. The live ledger was never in the way.
LIVE_AFTER=$(md5sum store/muhely-jobs.json 2>/dev/null | cut -d' ' -f1)
[ "$LIVE_BEFORE" = "$LIVE_AFTER" ] && ok "az eles naplo erintetlen" \
                                   || no "AZ ELES NAPLO MEGVALTOZOTT"

exit $fail
