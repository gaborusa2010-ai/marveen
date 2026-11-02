#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }
STORE_SRC=${STORE_SRC:-store}
echo "muhely-szamla-kor"

# #758: every functional case runs on a SYNTHETIC store with a fixed content
# (three clients, one of them mixed CIS, dates relative to today), so the result
# no longer depends on how many unbilled days the owner has at the moment. The
# live store is only read for the historical replay of the issued invoices
# 10016-10023, which cannot be synthesised; without it that one step is skipped
# and says so.
FIXTURE="$TMP/fixture"
python3 scripts/__tests__/muhely-szamla-kor-fixture.py "$FIXTURE" > /dev/null || { echo "  FAIL setup: fixture" >&2; exit 2; }

live_ok=1
for f in muhely-jobs.json muhely-ugyfelek.json muhely-business.json muhely-invoice-log.json; do
  [ -f "$STORE_SRC/$f" ] || live_ok=0
done
ls "$STORE_SRC"/muhely/invoices/10016-*.json >/dev/null 2>&1 || live_ok=0
for f in muhely-jobs.json muhely-ugyfelek.json muhely-business.json muhely-counters.json muhely-invoice-log.json; do
  [ -f "$STORE_SRC/$f" ] && md5sum "$STORE_SRC/$f" >> "$TMP/live-before"
done

sandbox() {
  # $2: the store to copy, the fixture by default.
  SANDBOX="$TMP/repo-$1"
  src=${2:-$FIXTURE}
  mkdir -p "$SANDBOX/scripts/muhely" "$SANDBOX/scripts/__tests__" "$SANDBOX/store"
  cp -a scripts/muhely/. "$SANDBOX/scripts/muhely/"
  cp scripts/__tests__/muhely-szamla-kor-regression.py "$SANDBOX/scripts/__tests__/"
  # Only what the invoicing run reads: the live store is gigabytes.
  cp -a "$src"/muhely-*.json "$SANDBOX/store/"
  mkdir -p "$SANDBOX/store/muhely"
  cp -a "$src/muhely/invoices" "$SANDBOX/store/muhely/"
}

sandbox main
export MUHELY_NO_DRIVE=1 MUHELY_NO_DESKTOP=1
RUN="$SANDBOX/scripts/muhely/szamla-kor.py"

# Preview cannot spend or mark anything, and must print DRAFT without a five-digit ref.
job=$(find "$SANDBOX/store/muhely/invoices" -name '10101-*.json' | head -1)
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
case $rc in 1|5) true;; *) false;; esac && [ "$before" = "$after" ] && [ ! -d "$SANDBOX/store/muhely/szamlazas-program/runs" ] \
  && grep -q Piszkozat "$TMP/dry.out" && ok "dry-run nem ir" || no "dry-run irt vagy nem listazott"

# Real draft creates seeds, hashed artifacts and a DRAFT PDF.
python3 "$RUN2" draft --date 2026-10-31 >"$TMP/real.out" 2>"$TMP/real.err"; rc=$?
rundir="$SANDBOX/store/muhely/szamlazas-program/runs/2026-10-31"
case $rc in 1|5) true;; *) false;; esac && [ -s "$rundir/run.json" ] && [ -s "$rundir/uzenet.txt" ] \
  && grep -q '"json_sha256"' "$rundir/run.json" && ok "valodi draft es manifest" || no "valodi draft"

# The mixed client (two plain days, two CIS days) is two invoices in the default
# run: its own key each, its own file each, and the CIS one states the deduction.
python3 - "$rundir" > "$TMP/mixed.out" 2>&1 <<'PY'
import json, os, sys
rd = sys.argv[1]
mine = {x["key"]: x for x in json.load(open(os.path.join(rd, "run.json")))["invoices"]
        if x["client"] == "Teszt Vegyes Epito"}
msg = open(os.path.join(rd, "uzenet.txt"), encoding="utf-8").read()
plain, cis = mine.get("teszt-vegyes-epito"), mine.get("teszt-vegyes-epito--cis")
assert plain and cis, sorted(mine)
assert plain["status"] == cis["status"] == "draft", (plain["status"], cis["status"])
assert plain["json_path"] != cis["json_path"] and all(os.path.exists(x["json_path"]) for x in (plain, cis))
assert (len(plain["days"]), len(cis["days"])) == (2, 2), (plain["days"], cis["days"])
assert (plain["subtotal"], plain["cis"], plain["total"]) == (400.0, 0, 400.0), plain
assert (cis["subtotal"], cis["cis"], cis["total"]) == (400.0, 80.0, 320.0), cis
assert msg.count("Teszt Vegyes Epito: 2 nap, 400.00 GBP.") == 2 and "CIS levonas: 80.00 GBP, fizetendo: 320.00 GBP" in msg, msg
PY
[ $? = 0 ] && ok "vegyes CIS ugyfel: ket piszkozat, 400 es 400-80" || { no "vegyes CIS ugyfel"; sed 's/^/       /' "$TMP/mixed.out"; }

# Changing a draft after its hash was messaged must block approval.
draft=$(python3 - "$rundir/run.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['invoices'][0]['json_path'])
PY
)
printf ' ' >> "$draft"
client=$(python3 - "$rundir/run.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['invoices'][0]['key'])
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

# jobs.py szamla's CLI output and JSON stay byte-identical: (a) to the builder
# it wraps, called directly, which holds after any merge; (b) to jobs.py before
# build_szamla existed (87c3c8ac), while that commit is in the object store.
sandbox jobs
ids=$(python3 - "$SANDBOX/store/muhely-jobs.json" <<'PY'
import json,sys
r=[x for x in json.load(open(sys.argv[1])) if x.get('stopped_at') and not x.get('invoice_ref') and not x.get('no_charge')]
c=r[0]['client']; print(*[x['id'] for x in r if x['client']==c and bool(x.get('cis'))==bool(r[0].get('cis'))][:2])
PY
)
[ -n "$ids" ] || no "jobs.py byte-teszt: nincs szamlazatlan nap a store-ban"
(cd "$SANDBOX" && python3 scripts/muhely/jobs.py szamla $ids --out out.json > stdout)
(cd "$SANDBOX" && python3 - $ids > inner.stdout <<'PY'
import argparse, importlib.util, sys
spec = importlib.util.spec_from_file_location("j", "scripts/muhely/jobs.py")
j = importlib.util.module_from_spec(spec); spec.loader.exec_module(j)
sys.exit(j._cmd_szamla_build(argparse.Namespace(ids=[int(x) for x in sys.argv[1:]], leiras=None, out="inner.json")))
PY
)
sed 's/inner\.json/out.json/g' "$SANDBOX/inner.stdout" > "$SANDBOX/inner.fixed"
[ -s "$SANDBOX/out.json" ] && cmp -s "$SANDBOX/inner.fixed" "$SANDBOX/stdout" && cmp -s "$SANDBOX/inner.json" "$SANDBOX/out.json" \
  && ok "jobs.py szamla = a belso epito, bajtra" || no "jobs.py szamla elter a belso epitotol"
if git cat-file -e 87c3c8ac:scripts/muhely/jobs.py 2>/dev/null; then
  mkdir -p "$TMP/old/scripts/muhely" "$TMP/old/store"
  git show 87c3c8ac:scripts/muhely/jobs.py > "$TMP/old/scripts/muhely/jobs.py"
  cp "$SANDBOX/store"/muhely-{jobs,ugyfelek,business}.json "$TMP/old/store/"
  (cd "$TMP/old" && python3 scripts/muhely/jobs.py szamla $ids --out out.json > stdout)
  ! cmp -s "$TMP/old/scripts/muhely/jobs.py" scripts/muhely/jobs.py \
    && cmp -s "$TMP/old/stdout" "$SANDBOX/stdout" && cmp -s "$TMP/old/out.json" "$SANDBOX/out.json" \
    && ok "jobs.py szamla = a 87c3c8ac elotti valtozat, bajtra" || no "jobs.py szamla elter a 87c3c8ac-tol"
else
  # Shallow clones (CI checkout without fetch-depth: 0) lack the old commit; say so, do not fail.
  echo "  SKIP jobs.py szamla vs 87c3c8ac: a commit nincs az object store-ban (sekely klon; fetch-depth: 0 vagy teljes klon kell hozza)"
fi

# Historical sources 10016-10023, including named 10018/10019 cases and a mutation control.
# Issued invoices do not change, so this is the one step that still reads the live store.
if [ "$live_ok" = 1 ]; then
  sandbox regression "$STORE_SRC"
  python3 "$SANDBOX/scripts/__tests__/muhely-szamla-kor-regression.py" "$SANDBOX" >"$TMP/r.o" 2>"$TMP/r.e"; rc=$?
  [ "$rc" = 0 ] && ok "10016-10023 torteneti replay" || { no "torteneti replay"; sed 's/^/       /' "$TMP/r.o"; sed 's/^/       /' "$TMP/r.e"; }
else
  echo "  SKIP 10016-10023 torteneti replay: $STORE_SRC has no issued workshop invoices (point STORE_SRC at the main checkout's store)"
fi

# SPEC 6.2 bad-input table and the approval path, one sandbox per case.
mkdir -p "$TMP/cases"
python3 scripts/__tests__/muhely-szamla-kor-cases.py "$PWD" "$FIXTURE" "$TMP/cases" >"$TMP/k.o" 2>&1; rc=$?
sed 's/^  /  case /' "$TMP/k.o"
n=$(grep -c '^  ok  ' "$TMP/k.o")
[ "$rc" = 0 ] && [ "$n" -ge 34 ] && ok "rossz bemenet es jovahagyas: $n eset" || no "rossz bemenet es jovahagyas ($n ok, rc=$rc)"

for f in muhely-jobs.json muhely-ugyfelek.json muhely-business.json muhely-counters.json muhely-invoice-log.json; do
  [ -f "$STORE_SRC/$f" ] && md5sum "$STORE_SRC/$f" >> "$TMP/live-after"
done
if [ -s "$TMP/live-before" ]; then
  cmp -s "$TMP/live-before" "$TMP/live-after" && ok "az eredeti store valtozatlan" || no "az eredeti store megvaltozott"
fi

exit $fail
