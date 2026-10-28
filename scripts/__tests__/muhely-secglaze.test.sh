#!/usr/bin/env bash
# secglaze.py (#720): secondary glazing cut list. Empty, too large, invalid and
# too small input must be refused with the documented exit code, and the sizes
# of a normal job must match figures worked out HERE from the sourced rules,
# not read back out of the script:
#   magnetic: sheet = frame outside - 20 mm per side (S2, South Seeds)
#   frame:    sheet = reveal - 15 mm per side (S4); head/sill = reveal width - 2;
#             jamb = reveal height - 2 x 44 - 2 (21 x 44 batten on edge, ESTIMATE)
# SCRIPT can point at another copy, so a negative control can run this file
# against a deliberately broken script.
set -u
cd "$(dirname "$0")/../.." || exit 1
SCRIPT=${SCRIPT:-scripts/muhely/secglaze.py}
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }
expect_exit() {  # $1 = expected code, $2 = label, rest = args
  local want=$1 label=$2; shift 2
  python3 "$SCRIPT" "$@" >"$TMP/out" 2>"$TMP/err"; local got=$?
  if [ "$got" = "$want" ]; then ok "$label (exit $got)"; else no "$label: exit $got, want $want: $(head -c 200 "$TMP/err")"; fi
}

echo "muhely-secglaze"

# -- empty
expect_exit 2 "no windows at all"
echo '{"windows": []}' > "$TMP/empty.json"
expect_exit 2 "job JSON with an empty window list" --job "$TMP/empty.json"
: > "$TMP/zero.json"
expect_exit 2 "empty job file" --job "$TMP/zero.json"
expect_exit 2 "missing job file" --job "$TMP/nincs-ilyen.json"

# -- invalid
expect_exit 3 "text instead of a number" -w "A=abcx900"
expect_exit 3 "zero width" -w "A=0x900"
expect_exit 3 "negative height" -w "A=900x-5"
expect_exit 3 "missing height" -w "A=900x"
expect_exit 3 "no NAME= part" -w "900x900"
expect_exit 3 "nan" -w "A=nanx900"
expect_exit 3 "zero quantity" -w "A=900x900x0"
expect_exit 3 "sheet thicker than the tape takes (S3: 2-4 mm)" -w "A=900x900" --thickness 6
echo '{"windows": [{"name": "A", "w": "900", "h": null}]}' > "$TMP/null.json"
expect_exit 3 "null height in JSON" --job "$TMP/null.json"
echo '[1, 2]' > "$TMP/list.json"
expect_exit 3 "job JSON is a list, not an object" --job "$TMP/list.json"

# -- too small / too large
expect_exit 3 "too small: magnetic 120 x 400 leaves an 80 mm sheet" -w "A=120x400" --method magnetic
expect_exit 3 "frame shorter than two battens" -w "A=600x80"
expect_exit 3 "too large: wider than the largest stock sheet (3000)" -w "A=3500x1000"
expect_exit 4 "fits the hard limit, not the 2050 x 1525 stock sheet" -w "A=2500x1800"
grep -q "NEM FER RA" "$TMP/out" && ok "oversize sheet is named in the report" || no "oversize sheet not named"

# -- normal job, sizes from first principles
cat > "$TMP/job.json" <<'J'
{"job": "teszt", "windows": [
  {"name": "Nappali", "w": 900, "h": 1200, "method": "frame"},
  {"name": "Halo", "w": 600, "h": 900, "qty": 2, "method": "frame"},
  {"name": "Furdo", "w": 500, "h": 700, "method": "magnetic"}]}
J
expect_exit 0 "normal three-window job" --job "$TMP/job.json" --json "$TMP/r.json" --pdf "$TMP/r.pdf"
python3 - "$TMP/r.json" <<'PY' || fail=1
import json, sys
r = json.load(open(sys.argv[1]))
w = {x["name"]: x for x in r["windows"]}
bad = []
def eq(label, got, want):
    if abs(got - want) > 1e-9:
        bad.append(f"{label}: {got} != {want}")
eq("Nappali sheet w", w["Nappali"]["sheet"]["w"], 900 - 2 * 15)
eq("Nappali sheet h", w["Nappali"]["sheet"]["h"], 1200 - 2 * 15)
eq("Furdo sheet w", w["Furdo"]["sheet"]["w"], 500 - 2 * 20)
eq("Furdo sheet h", w["Furdo"]["sheet"]["h"], 700 - 2 * 20)
b = {x["name"]: x for x in w["Nappali"]["battens"]}
eq("Nappali head", b["Nappali felso+also / head+sill"]["len"], 900 - 2)
eq("Nappali jamb", b["Nappali oldal / jamb"]["len"], 1200 - 2 * 44 - 2)
h = {x["name"]: x for x in w["Halo"]["battens"]}
eq("Halo jamb qty", h["Halo oldal / jamb"]["qty"], 4)
if w["Furdo"]["battens"]:
    bad.append("magnetic window got battens")
perim = 2 * (870 + 1170) + 2 * 2 * (570 + 870) + 2 * (460 + 660)
eq("tape m", r["tape"]["each_m"], round(perim * 1.10 / 1000, 2))
for x in bad:
    print("  FAIL", x)
if not bad:
    print("  ok   sizes match first principles")
sys.exit(1 if bad else 0)
PY
head -c 5 "$TMP/r.pdf" | grep -q '%PDF-' && ok "PDF written" || no "PDF missing"
python3 scripts/muhely/pdftext.py "$TMP/r.pdf" > "$TMP/r.txt" 2>/dev/null
for t in "FORRÁSOK / SOURCES" "BECSLÉS / ESTIMATE" "LÉCKERETES ABLAK" "MAGNETIC ON THE FRAME" "cse.org.uk"; do
  grep -q "$t" "$TMP/r.txt" && ok "PDF has: $t" || no "PDF lacks: $t"
done

# -- each source says how it was read (Marveen msg 14442): S2/S3/S5 from the page,
#    S4 image-only PDF stays a search result; no source may go without a label
python3 - "$SCRIPT" <<'PY' || fail=1
import importlib.util, sys
spec = importlib.util.spec_from_file_location("sg", sys.argv[1]); m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
want = {"S1": "search", "S2": "page", "S3": "page", "S4": "search", "S5": "page", "S6": "search"}
got = {k: m.SOURCE_READ.get(k) for k in m.SOURCES}
print("  ok   per-source read status" if got == want else f"  FAIL SOURCE_READ = {got}")
sys.exit(0 if got == want else 1)
PY
n_page=$(grep -c "read from the page" "$TMP/r.txt"); n_search=$(grep -c "search result only" "$TMP/r.txt")
[ "$n_page" = 3 ] && [ "$n_search" = 3 ] && ok "PDF labels 3 sources page, 3 search" || no "PDF labels: page=$n_page search=$n_search, want 3/3"

# -- 2 mm sheet on a big window warns (ESTIMATE)
python3 "$SCRIPT" -w "A=1000x1200" --thickness 2 > "$TMP/thin" 2>&1
grep -q "4 mm javasolt" "$TMP/thin" && ok "2 mm sheet on a 1000 x 1200 window warns" || no "no 2 mm warning"

# -- tape rolls: 30.5 m of tape is one 30 m roll and one 5 m roll (S3 lengths)
python3 - "$SCRIPT" <<'PY' || fail=1
import importlib.util, sys
spec = importlib.util.spec_from_file_location("sg", sys.argv[1]); m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
got = m.rolls_for(30500)
want = {"30 m": 1, "15 m": 0, "5 m": 1}
print("  ok   30.5 m tape -> 30 m + 5 m" if got == want else f"  FAIL rolls_for(30500) = {got}")
sys.exit(0 if got == want else 1)
PY

[ $fail = 0 ] && echo "PASS" || echo "FAILED"
exit $fail
