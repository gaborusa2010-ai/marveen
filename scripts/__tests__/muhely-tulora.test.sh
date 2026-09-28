#!/usr/bin/env bash
# Overtime: the day rate buys NINE HOURS of presence, and the rest is the day rate
# over EIGHT, per hour.
#
# The owner, 2026-09-09 12:45: "akkor 7-tol 4-ig napiber, utana minden extra ora az
# orabérben (200 font / 8 ora = 25 font/ora)". And on 2026-09-10 08:07, in the group
# chat, after we had put a clock-time reading in front of him (message 506):
# "7-tol 4-ig az 9 ora , 8-tol 5-ig az is 9 ora = napidij 200 font...". So the
# boundary is the LENGTH of the day, not four o'clock: an 08:00-17:00 installation
# day is an ordinary day at 200.
#
# The divisor is EIGHT because a nine-hour day is eight PAID hours. Over nine it would
# underpay every overtime hour by about eleven per cent.
#
# ONE REFUSAL is left, and case 4 measures it: a multi-day record, where the rule
# prices one day of presence and cannot say how the hours fall across several.
#
# TWO OTHER refusals used to live here, and both are gone ON PURPOSE. A start after
# 16:00 was "outside the rule" only while the rule named a clock time. A start that
# was not 07:00 was refused on a reading the owner has now rejected outright. Cases
# 5 and 6b check that neither refuses any more: a refusal that outlives its reason
# looks exactly like caution, and costs money in the same silence.
#

set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

# A COPY OF THE SCRIPT IN A COPY OF THE TREE, never the live ledger. jobs.py derives
# REPO from its own path (jobs.py:36), so a copy three levels down inside $TMP reads
# and writes $TMP/store -- measured, and the reason this shape is used at all.
SANDBOX="$TMP/repo"
mkdir -p "$SANDBOX/scripts/muhely" "$SANDBOX/store"
cp scripts/muhely/jobs.py "$SANDBOX/scripts/muhely/"
cp store/muhely-business.json "$SANDBOX/store/" 2>/dev/null || true

# A COPY of the ledger, never the live one.
led() {  # $1 start "HH:MM"  $2 stop "HH:MM"  $3 days-or-empty
  python3 - "$1" "$2" "${3:-}" "$SANDBOX/store/muhely-jobs.json" <<'PY'
import json, sys, time
from datetime import datetime
s, e, days, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
def ts(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return time.mktime(datetime(2026, 9, 3, h, m).timetuple())
r = {"id": 1, "client": "Teszt", "job": "teszt", "started_at": ts(s), "stopped_at": ts(e),
     "rate": 200.0, "billing": "daily", "cis": False}
if days:
    r["days"] = int(days)
json.dump([r], open(out, "w"))
PY
}

run() {  # fills OUT/ERR/RC from `jobs.py invoice 1`
  rm -f "$TMP/o.json"
  OUT=$(python3 "$SANDBOX/scripts/muhely/jobs.py" invoice 1 --out "$TMP/o.json" 2>"$TMP/err"); RC=$?
  ERR=$(cat "$TMP/err")
}
otline() { python3 - "$TMP/o.json" <<'PY'
import json, sys
try: d = json.load(open(sys.argv[1]))
except Exception: print(""); raise SystemExit
for l in d.get("lines", []):
    if "Overtime" in l.get("desc", ""):
        print(f"{l['qty']:.4f} {l['unit']:.4f}")
PY
}

echo "muhely-tulora"

# 1. a plain workshop day: 07:00-16:00 -> no overtime line at all
led 07:00 16:00
run
[ -n "$(otline)" ] && no "07-16: nem szabad tulora-sor" || ok "07-16: nincs tulora-sor"

# 2. 07:00-18:00 -> exactly two hours at 25
led 07:00 18:00
run
case "$(otline)" in
  "2.0000 25.0000") ok "07-18: 2 ora x 25" ;;
  *) no "07-18: varhato '2.0000 25.0000', kapott '$(otline)'" ;;
esac

# 3. the quantity stays EXACT, not rounded: 07:00-16:45 is 0.75 h
led 07:00 16:45
run
case "$(otline)" in
  "0.7500 25.0000") ok "07-16:45: a qty pontos tort (0.75)" ;;
  *) no "07-16:45: varhato '0.7500 25.0000', kapott '$(otline)'" ;;
esac

# 4. REFUSAL: multi-day record
led 07:00 18:00 3
run
printf '%s' "$ERR" | grep -q "NEM SZAMOLOM EL" \
  && { [ -z "$(otline)" ] && ok "tobb napos: elutasit, es nincs sor" || no "tobb napos: sort tett be"; } \
  || no "tobb napos: nem utasitotta el"

# 5. A LATE START IS NO LONGER A SPECIAL CASE. With a clock boundary a 22:00 start
#    was outside the rule and had to be refused; with a length there is no boundary
#    to be outside of, and an hour and a half of presence is simply a short day.
#    Nine hours still buys the day rate, so the overtime is zero, not negative.
led 22:00 23:30
run
[ "$RC" = "0" ] && [ -z "$(otline)" ] \
  && ok "keso kezdes: rovid nap, nincs tulora-sor" \
  || no "keso kezdes: exit $RC, tulora-sor '$(otline)'"

# 6. THE SITE DAY. 08:00-17:34 is one hour and 34 minutes past the fixed 16:00.
#    This case used to be a refusal, on the reading that a nine-hour presence is
#    an ordinary day whatever hour it starts. The owner settled it against that
#    reading (2026-09-10 07:43, message_id 497, answering whether the day rate on
#    an 08:00 installation day runs out at four or five): "Hivatalosan 4-kor,de
#    van hogy tovabb tart a munka...". So the hour of the start does not move the
#    finishing time, and this is a real invoiced overtime line.
led 08:00 17:34
run
case "$(otline)" in
  "0.5667 25.0000") ok "08:00-17:34 site day: 0.5667 ora x 25 (9,57 ora jelenlet)" ;;
  *) no "08:00-17:34: '$(otline)' (vart: 0.5667 25.0000)" ;;
esac

# 6b. THE CASE THE OWNER SPELLED OUT, and the one we got wrong for half an hour:
#     08:00-17:00 is nine hours, the same nine as 07:00-16:00, so it is a plain day.
#     "7-tol 4-ig az 9 ora , 8-tol 5-ig az is 9 ora = napidij 200 font" (2026-09-10
#     08:07, group message 506).
led 08:00 17:00
run
[ -z "$(otline)" ] && ok "08:00-17:00: rendes nap, nincs tulora-sor" \
  || no "08:00-17:00: tulora-sort tett be ('$(otline)')"

# 7. The refusal must reach the EXIT CODE, not only stderr. An invoice with an
#    unresolved overtime line is incomplete, and anything checking $? has to see it.
#    The case moved from an 08:00 start to a multi-day record when the owner
#    settled the start-hour question: the exit code still has to carry a refusal,
#    so it is measured on a refusal that REMAINS one.
led 07:00 19:00 2
run
[ "$RC" = "4" ] && ok "eldontetlen tulora -> exit 4" || no "eldontetlen tulora -> exit $RC (nem 4)"

# 8. NEGATIVE CONTROL for 7: a clean day must still exit 0, otherwise the check
#    would "pass" by failing on everything.
led 07:00 16:00
run
[ "$RC" = "0" ] && ok "tiszta nap -> exit 0" || no "tiszta nap -> exit $RC"

exit $fail
