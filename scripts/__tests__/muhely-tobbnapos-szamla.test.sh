#!/usr/bin/env bash
# `jobs.py szamla <id...>` -- several work days into ONE invoice input.
#
# Every multi-day invoice this business has issued was typed by hand, including
# 10016 on 2026-09-20: five dates, five descriptions and a 1000 pound total,
# none of it checked against the ledger. The command exists so that stops.
#
# The cases that matter are the ones a hand-typed file gets wrong:
#   1. the total and the day count come from the LEDGER, not from memory
#   2. `_job_ids` is there, so invoice.py can mark those days billed
#   3. two customers on one invoice is refused, not merged
#   4. the addressee is the full invoice one, not the ledger's short name
#   5. an unknown customer still works, but SAYS the address is missing
#   6. a description carried over from the Hungarian ledger is NAMED, because
#      nothing here can translate it and the customer reads it
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

LIVE=$(md5sum store/muhely-jobs.json | cut -d' ' -f1)

S="$TMP/repo"; mkdir -p "$S/scripts/muhely" "$S/store"
cp scripts/muhely/*.py "$S/scripts/muhely/"
cp store/muhely-business.json "$S/store/"

cat > "$S/store/muhely-ugyfelek.json" <<'JSON'
{"ugyfelek": {"Probe Ltd": {"name": "Mr P Probe, Probe Ltd",
                            "address": ["1 Test Rd", "TE5 7ER"]}}}
JSON

python3 - "$S/store" <<'PY'
import json, sys, time
base = time.time() - 9 * 86400
rows = []
for i, (client, job) in enumerate(
        [("Probe Ltd", "Muhelymunka: szekrenyajto"),
         ("Probe Ltd", "Muhelymunka: szekrenyajto"),
         ("Probe Ltd", "Muhelymunka: polcok"),
         ("Masik Kft", "Muhelymunka: mas ugyfel"),
         ("Ismeretlen Bt", "Muhelymunka: uj ugyfel")], start=1):
    st = base + i * 86400
    rows.append({"id": i, "client": client, "job": job, "address": "",
                 "started_at": st, "stopped_at": st + 9 * 3600, "rate": 200.0,
                 "cis": False, "billing": "daily", "days": None, "photos": [],
                 "notes": [], "invoice_ref": None, "no_charge": None})
json.dump(rows, open(sys.argv[1] + "/muhely-jobs.json", "w"), indent=2)
PY

echo "muhely-tobbnapos-szamla"

echo "== 1. harom nap, egy ugyfel"
OUT=$( cd "$S" && python3 scripts/muhely/jobs.py szamla 1 2 3 --out j.json 2>&1 ); RC=$?
[ $RC -eq 0 ] && ok "exit 0" || no "exit $RC"
echo "$OUT" | grep -q "600.00" && ok "az osszeg 600.00 (3 x 200)" \
                               || no "rossz osszeg: $(echo "$OUT" | grep -i osszesen)"

echo "== 2. a szerkezet, amit az invoice.py var"
python3 - "$S/j.json" <<'PY' && ok "cimzett, _job_ids es a sorok rendben" || no "hibas job fajl"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["_job_ids"] == [1, 2, 3], d.get("_job_ids")
assert d["client"]["name"] == "Mr P Probe, Probe Ltd", d["client"]
assert d["client"]["address"] == ["1 Test Rd", "TE5 7ER"], d["client"]
assert len(d["lines"]) == 3, len(d["lines"])
assert round(sum(l["qty"] * l["unit"] for l in d["lines"]), 2) == 600.0
# every line starts with its own date
import re
assert all(re.match(r"\d\d/\d\d/\d\d  ", l["desc"]) for l in d["lines"]), d["lines"]
PY

echo "== 3. NEGATIV KONTROLL: ket ugyfel egy szamlan"
OUT3=$( cd "$S" && python3 scripts/muhely/jobs.py szamla 1 4 --out j2.json 2>&1 ); RC3=$?
[ $RC3 -ne 0 ] && ok "elutasitja (exit $RC3)" || no "osszefogta ket ugyfel napjait"
[ -f "$S/j2.json" ] && no "megis kiirt egy fajlt" || ok "es nem irt ki fajlt"
echo "$OUT3" | grep -q "TOBB UGYFEL" && ok "es megmondja, miert" || no "nem mondta meg"

echo "== 4. ismeretlen ugyfel: megy, de SZOL"
OUT4=$( cd "$S" && python3 scripts/muhely/jobs.py szamla 5 --out j3.json 2>&1 ); RC4=$?
[ $RC4 -eq 0 ] && ok "az uj ugyfel is szamlazhato" || no "elakadt: exit $RC4"
echo "$OUT4" | grep -q "muhely-ugyfelek.json" && ok "es szol a hianyzo cimrol" \
                                              || no "csendben vitte a rovid nevet"
python3 - "$S/j3.json" <<'PY' && ok "a rovid nev ment tovabb, cim nelkul" || no "rossz cimzett"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["client"]["name"] == "Ismeretlen Bt", d["client"]
assert d["client"]["address"] == [], d["client"]
PY

echo "== 5. a magyar leirast MEGNEVEZI"
echo "$OUT" | grep -q "MUNKANAPLOBOL jon, magyarul" && ok "figyelmeztet a nyers szovegre" \
                                                    || no "csendben kikuldene magyarul"

echo "== 6. NEGATIV KONTROLL: nem letezo nap"
OUT6=$( cd "$S" && python3 scripts/muhely/jobs.py szamla 1 99 --out j4.json 2>&1 ); RC6=$?
[ $RC6 -ne 0 ] && ok "elutasitja (exit $RC6)" || no "atengedte a 99-et"
[ -f "$S/j4.json" ] && no "megis kiirt egy fajlt" || ok "es nem irt ki fajlt"

echo "== 7. az ELES munkanaplo erintetlen"
[ "$LIVE" = "$(md5sum store/muhely-jobs.json | cut -d' ' -f1)" ] \
  && ok "muhely-jobs.json valtozatlan" || no "AZ ELES MUNKANAPLO VALTOZOTT"

[ $fail -eq 0 ] && echo "  MIND RENDBEN" || echo "  VOLT HIBA"
exit $fail
