#!/usr/bin/env bash
# An edging field that says "tbc" is an undecided SIZE, not a note.
#
# Each banded edge takes 2 mm off THAT dimension, so a row whose edging is still
# open has no final width or height -- but it does not look unfinished, because
# "tbc" is a non-empty string and every other check passes it. The whole-list
# check ("NINCS ELSZALAG OSZLOP") only fires when NOT ONE row carries edging,
# so a single undecided row falls between the two.
#
# Measured 2026-09-09 on the live Lyford B-02 list: six rows carried "tbc" and
# the checker reported clean.
#
# The check must also stay SILENT on a decision. "none, painted" and
# "2 mm front edge" are answers; a checker that fires on every edging string
# gets ignored, which is how two earlier false alarms left listcheck.py.
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

list() {  # $1 = the edging value of the single part row
  python3 - "$1" "$TMP/c.json" <<'PY'
import json, sys
edg, out = sys.argv[1], sys.argv[2]
job = {"job": "teszt", "stock": [{"material": "18ML", "w": 2440, "h": 1220}],
       "parts": [{"name": "fiokoldal", "w": 490, "h": 130, "qty": 4,
                  "grain": "w", "material": "18ML", "edging": edg}]}
json.dump(job, open(out, "w"))
PY
}

fires() { python3 scripts/muhely/listcheck.py "$TMP/c.json" 2>&1 | grep -q "ELDONTETLEN ELSZALAG"; }

echo "muhely-eldontetlen-elszalag"

# 1. a placeholder MUST fire -- otherwise the check cannot say "no"
for v in tbc TBC "  tbc  " tbd "?" "-" "n/a"; do
  list "$v"
  fires && ok "megszolal: \"$v\"" || no "NEM szolalt meg: \"$v\""
done

# 2. a real decision must stay SILENT -- otherwise it is a wolf-crier
for v in "none" "none, painted" "2 mm front edge" "2 mm, mindket hosszu el"; do
  list "$v"
  fires && no "hamis riasztas: \"$v\"" || ok "hallgat: \"$v\""
done

# 3. a row with NO edging key at all is the nesting input's shape, not an
#    undecided decision. The whole-list check covers that case; this one must
#    not double-report it.
python3 - "$TMP/c.json" <<'PY'
import json, sys
job = {"job": "teszt", "stock": [{"material": "18ML", "w": 2440, "h": 1220}],
       "parts": [{"name": "fiokoldal", "w": 490, "h": 130, "qty": 4,
                  "grain": "w", "material": "18ML"}]}
json.dump(job, open(sys.argv[1], "w"))
PY
fires && no "hianyzo kulcsra nem szabad elsulnie" || ok "hianyzo edging kulcs: hallgat"

exit $fail
