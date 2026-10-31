#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }
STORE_SRC=${STORE_SRC:-store}
echo "muhely-szamla-kor"

for f in muhely-jobs.json muhely-ugyfelek.json muhely-business.json muhely-counters.json muhely-invoice-log.json; do
  [ -f "$STORE_SRC/$f" ] && md5sum "$STORE_SRC/$f" >> "$TMP/live-before"
done

sandbox() {
  SANDBOX="$TMP/repo-$1"
  mkdir -p "$SANDBOX/scripts/muhely" "$SANDBOX/scripts/__tests__" "$SANDBOX/store"
  cp -a scripts/muhely/. "$SANDBOX/scripts/muhely/"
  cp scripts/__tests__/muhely-szamla-kor-regression.py "$SANDBOX/scripts/__tests__/"
  # Only what the invoicing run reads: the live store is gigabytes.
  cp -a "$STORE_SRC"/muhely-*.json "$SANDBOX/store/"
  mkdir -p "$SANDBOX/store/muhely"
  cp -a "$STORE_SRC/muhely/invoices" "$SANDBOX/store/muhely/"
}

sandbox main
export MUHELY_NO_DRIVE=1 MUHELY_NO_DESKTOP=1
RUN="$SANDBOX/scripts/muhely/szamla-kor.py"

# Preview cannot spend or mark anything, and must print DRAFT without a five-digit ref.
job=$(find "$SANDBOX/store/muhely/invoices" -name '10016-*.json' | head -1)
for f in muhely-counters.json muhely-invoice-log.json muhely-jobs.json; do md5sum "$SANDBOX/store/$f"; done > "$TMP/pre"
python3 "$SANDBOX/scripts/muhely/invoice.py" preview "$job" --out "$TMP/preview.pdf" > "$TMP/preview.out" 2> "$TMP/preview.err"; rc=$?
for f in muhely-counters.json muhely-invoice-log.json muhely-jobs.json; do md5sum "$SANDBOX/store/$f"; done > "$TMP/post"
txt=$(python3 "$SANDBOX/scripts/muhely/pdftext.py" "$TMP/preview.pdf")
[ "$rc" = 0 ] && cmp -s "$TMP/pre" "$TMP/post" && printf '%s' "$txt" | grep -q DRAFT \
  && ! printf '%s' "$txt" | grep -Eq '(^|[^0-9])[0-9]{5}([^0-9]|$)' \
  && ok "preview: DRAFT es nincs mellekhatas" || no "preview mellekhatas vagy referencia"

# The renderer rejects 65 characters before creating a PDF or spending a number.
python3 - "$job" "$TMP/long.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['lines'][0]['desc']='x'*65
json.dump(d,open(sys.argv[2],'w'))
PY
rm -f "$TMP/long.pdf"
python3 "$SANDBOX/scripts/muhely/invoice.py" preview "$TMP/long.json" --out "$TMP/long.pdf" >"$TMP/o" 2>"$TMP/e"; rc=$?
[ "$rc" = 2 ] && [ ! -e "$TMP/long.pdf" ] && grep -q "$(printf 'x%.0s' {1..65})" "$TMP/e" \
  && ok "65 karakter: visszautasitas a rajzolas elott" || no "65 karakteres sor"

# Dry-run reports the live candidates but creates no config or run directory.
sandbox dry
RUN2="$SANDBOX/scripts/muhely/szamla-kor.py"
before=$(find "$SANDBOX/store" -type f -exec md5sum {} + | sort | md5sum)
python3 "$RUN2" draft --dry-run --date 2026-10-31 >"$TMP/dry.out" 2>"$TMP/dry.err"; rc=$?
after=$(find "$SANDBOX/store" -type f -exec md5sum {} + | sort | md5sum)
[ "$rc" = 1 ] && [ "$before" = "$after" ] && [ ! -d "$SANDBOX/store/muhely/szamlazas-program/runs" ] \
  && grep -q Piszkozat "$TMP/dry.out" && ok "dry-run nem ir" || no "dry-run irt vagy nem listazott"

# Real draft creates seeds, hashed artifacts and a DRAFT PDF.
python3 "$RUN2" draft --date 2026-10-31 >"$TMP/real.out" 2>"$TMP/real.err"; rc=$?
rundir="$SANDBOX/store/muhely/szamlazas-program/runs/2026-10-31"
[ "$rc" = 1 ] && [ -s "$rundir/run.json" ] && [ -s "$rundir/uzenet.txt" ] \
  && grep -q '"json_sha256"' "$rundir/run.json" && ok "valodi draft es manifest" || no "valodi draft"

# Changing a draft after its hash was messaged must block approval.
draft=$(python3 - "$rundir/run.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['invoices'][0]['json_path'])
PY
)
printf ' ' >> "$draft"
client=$(python3 - "$rundir/run.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['invoices'][0]['client'])
PY
)
python3 "$RUN2" approve "$rundir" "$client" >"$TMP/a.o" 2>"$TMP/a.e"; rc=$?
[ "$rc" = 6 ] && grep -q 'piszkozat valtozott' "$TMP/a.e" && ok "modositott draft blokkolva" || no "draft hash guard"

# Bad JSON names the file/position and creates no runs; version and unknown step are code 2.
sandbox bad
printf '[{' > "$SANDBOX/store/muhely-jobs.json"
python3 "$SANDBOX/scripts/muhely/szamla-kor.py" draft --date 2026-11-01 >"$TMP/b.o" 2>"$TMP/b.e"; rc=$?
[ "$rc" = 2 ] && grep -q 'muhely-jobs.json.*position' "$TMP/b.e" && [ ! -d "$SANDBOX/store/muhely/szamlazas-program/runs" ] \
  && ok "csonka ledger: kod 2, helyzettel" || no "csonka ledger"

sandbox config
mkdir -p "$SANDBOX/store/muhely/szamlazas-program/config"
cp scripts/muhely/szamla-kor-config/*.toml "$SANDBOX/store/muhely/szamlazas-program/config/"
sed -i 's/"draft"/"nincsilyen"/' "$SANDBOX/store/muhely/szamlazas-program/config/szamla.toml"
python3 "$SANDBOX/scripts/muhely/szamla-kor.py" draft --date 2026-11-02 >"$TMP/c.o" 2>"$TMP/c.e"; rc=$?
[ "$rc" = 2 ] && grep -q 'ismeretlen lepes' "$TMP/c.e" && ok "ismeretlen lepes: kod 2" || no "ismeretlen lepes"

# jobs.py's public CLI output and JSON remain byte-identical to HEAD.
sandbox jobs
mkdir -p "$TMP/old/scripts/muhely" "$TMP/old/store"
git show HEAD:scripts/muhely/jobs.py > "$TMP/old/scripts/muhely/jobs.py"
cp "$SANDBOX/store"/muhely-{jobs,ugyfelek,business}.json "$TMP/old/store/"
ids=$(python3 - "$SANDBOX/store/muhely-jobs.json" <<'PY'
import json,sys
r=[x for x in json.load(open(sys.argv[1])) if x.get('stopped_at') and not x.get('invoice_ref') and not x.get('no_charge')]
c=r[0]['client']; print(*[x['id'] for x in r if x['client']==c and bool(x.get('cis'))==bool(r[0].get('cis'))][:2])
PY
)
(cd "$TMP/old" && python3 scripts/muhely/jobs.py szamla $ids --out out.json > stdout)
(cd "$SANDBOX" && python3 scripts/muhely/jobs.py szamla $ids --out out.json > stdout)
cmp -s "$TMP/old/stdout" "$SANDBOX/stdout" && cmp -s "$TMP/old/out.json" "$SANDBOX/out.json" \
  && ok "jobs.py szamla byte-identical" || no "jobs.py szamla regresszio"

# Historical sources 10016-10023, including named 10018/10019 cases and a mutation control.
sandbox regression
python3 "$SANDBOX/scripts/__tests__/muhely-szamla-kor-regression.py" "$SANDBOX" >"$TMP/r.o" 2>"$TMP/r.e"; rc=$?
[ "$rc" = 0 ] && ok "10016-10023 torteneti replay" || { no "torteneti replay"; sed 's/^/       /' "$TMP/r.o"; sed 's/^/       /' "$TMP/r.e"; }

# SPEC 6.2 bad-input table and the approval path, one sandbox per case.
mkdir -p "$TMP/cases"
python3 scripts/__tests__/muhely-szamla-kor-cases.py "$PWD" "$STORE_SRC" "$TMP/cases" >"$TMP/k.o" 2>&1; rc=$?
sed 's/^  /  case /' "$TMP/k.o"
n=$(grep -c '^  ok  ' "$TMP/k.o")
[ "$rc" = 0 ] && [ "$n" -ge 17 ] && ok "rossz bemenet es jovahagyas: $n eset" || no "rossz bemenet es jovahagyas ($n ok, rc=$rc)"

for f in muhely-jobs.json muhely-ugyfelek.json muhely-business.json muhely-counters.json muhely-invoice-log.json; do
  [ -f "$STORE_SRC/$f" ] && md5sum "$STORE_SRC/$f" >> "$TMP/live-after"
done
cmp -s "$TMP/live-before" "$TMP/live-after" && ok "az eredeti store valtozatlan" || no "az eredeti store megvaltozott"

exit $fail
