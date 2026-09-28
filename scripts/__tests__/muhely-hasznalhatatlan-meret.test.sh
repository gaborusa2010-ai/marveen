#!/usr/bin/env bash
# A size field that is not a number must be a FINDING, never a crash.
#
# Measured 2026-09-12, on a real proposal: put a row in with `w: "tbc"` -- the
# same placeholder that is a decision-marker for edging -- and listcheck.py died
# at a bare float() with a traceback, AND exited 1. That is the same exit code a
# clean finding-report uses, so a caller reading only the exit code sees a
# working checker while every finding after the crash is gone, including the
# undecided-edging warning the row was added for.
#
# Four separate bare float() calls could kill the run (lines 222, 236, 257, 260
# before the fix), so the rows are filtered ONCE into `numeric_parts` and every
# size-based check iterates that. A check added later cannot fall into the hole.
#
# The zero case is separate on purpose: `w: 0` does not crash, and today it only
# surfaces if the SAME row also has undecided edging. Decide the edging and the
# zero goes silent -- so it needs its own finding.
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

# $1 = w value (JSON literal), $2 = h value (JSON literal)
list() {
  python3 - "$1" "$2" "$TMP/c.json" <<'PY'
import json, sys
w, h, out = json.loads(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
job = {"job": "teszt", "stock": [{"material": "18ML", "w": 2440, "h": 1220}],
       "parts": [{"name": "jo sor", "w": 490.0, "h": 130.0, "qty": 4,
                  "grain": "w", "material": "18ML", "edging": "tbc"},
                 {"name": "vizsgalt sor", "w": w, "h": h, "qty": 1,
                  "grain": "w", "material": "18ML", "edging": "none"}]}
json.dump(job, open(out, "w"))
PY
}

run() { python3 scripts/muhely/listcheck.py "$TMP/c.json" > "$TMP/out.txt" 2>&1; echo $?; }

echo "muhely-hasznalhatatlan-meret"

# 1. A non-numeric size fires, and does NOT crash.
for v in '"tbc"' '"tbd"' '"?"' '"kesobb"' 'null'; do
  list "$v" '130.0'; code=$(run)
  grep -q Traceback "$TMP/out.txt" && no "OSSZEOMLOTT: w=$v" || {
    if [ "$v" = "null" ]; then
      # a missing number is 0.0, which is the NULLA MERET case, not NEM SZAM
      grep -q "NULLA MERET" "$TMP/out.txt" && ok "nulla meretkent jelzi: w=$v" || no "nem jelezte: w=$v"
    else
      grep -q "NEM SZAM MERET" "$TMP/out.txt" && ok "megszolal, nem omlik ossze: w=$v" || no "nem jelezte: w=$v"
    fi
  }
  [ "$code" = "1" ] || no "rossz exit kod ($code): w=$v"
done

# 2. THE POINT OF THE FIX: the other findings must SURVIVE the bad row.
list '"tbc"' '"tbc"'; run > /dev/null
grep -q "ELDONTETLEN ELSZALAG" "$TMP/out.txt" \
  && ok "a rossz sor utan is kijon az elszalag-kifogas" \
  || no "a rossz sor elnyelte a tobbi kifogast"

# 3. Zero and negative fire on their own, without leaning on edging.
for v in '0' '0.0' '-5'; do
  list "$v" '130.0'; run > /dev/null
  grep -q "NULLA MERET" "$TMP/out.txt" && ok "megszolal: w=$v" || no "NEM szolalt meg: w=$v"
done

# 4. It must be able to say NO: a clean list stays clean, exit 0.
python3 - "$TMP/c.json" <<'PY'
import json, sys
job = {"job": "teszt", "stock": [{"material": "18ML", "w": 2440, "h": 1220}],
       "parts": [{"name": "jo sor", "w": 490.0, "h": 130.0, "qty": 4,
                  "grain": "w", "material": "18ML", "edging": "2 mm front edge"}]}
json.dump(job, open(sys.argv[1], "w"))
PY
code=$(run)
grep -qE "NEM SZAM MERET|NULLA MERET" "$TMP/out.txt" && no "hamis riasztas tiszta listan" || ok "tiszta lista: hallgat"
[ "$code" = "0" ] || no "tiszta listan rossz exit kod ($code)"

exit $fail
