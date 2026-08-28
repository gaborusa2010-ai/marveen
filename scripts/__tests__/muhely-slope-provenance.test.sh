#!/usr/bin/env bash
# Where a raked job's stair angle came from must survive the whole chain.
#
# A quote is written before anyone can measure, so its stair figure is an
# assumption. Win the job, generate the final cut list from the same spec weeks
# later, and the assumption arrives at the saw as a bare number. On a rake that
# is not one part wrong, it is EVERY upright wrong.
#
# Guarantee: carcass.py ALWAYS writes slope_source, and listcheck.py refuses an
# assumed one. The last case documents where the fallback heuristic stops.
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

spec() {  # $1 = source value or "none"
  python3 - "$1" "$TMP/spec.json" <<'PY'
import json, sys
src, out = sys.argv[1], sys.argv[2]
s = {"job": "teszt", "w": 1400, "h": 1950, "d": 550, "t": 18,
     "back": {"type": "applied", "t": 6}, "dividers": {"count": 1},
     "slope": {"rise": 190, "run": 250}}
if src != "none":
    s["slope"]["source"] = src
json.dump(s, open(out, "w"))
PY
}
srcof() { python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('slope_source'))" "$1"; }

echo "muhely-slope-provenance"

# 1. measured -> passes
spec mert
python3 scripts/muhely/carcass.py "$TMP/spec.json" --cutlist "$TMP/c.json" >/dev/null 2>&1
[ "$(srcof "$TMP/c.json")" = "mert" ] && ok "mert -> slope_source=mert" || no "mert -> slope_source"
python3 scripts/muhely/listcheck.py "$TMP/c.json" 2>&1 | grep -q "SZOG" \
  && no "mert nem adhat szog-talalatot" || ok "mert -> nincs szog-talalat"

# 2. missing source is an ASSUMPTION, not a pass
spec none
python3 scripts/muhely/carcass.py "$TMP/spec.json" --cutlist "$TMP/c.json" >/dev/null 2>&1
[ "$(srcof "$TMP/c.json")" = "feltetelezett" ] \
  && ok "hianyzo source -> feltetelezett" || no "hianyzo source -> feltetelezett"
python3 scripts/muhely/listcheck.py "$TMP/c.json" >/dev/null 2>&1
[ $? -eq 1 ] && ok "feltetelezett -> listcheck exit 1" || no "feltetelezett -> exit 1"
python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
sys.exit(0 if any('FELTETELEZETT' in str(p.get('note','')) for p in d['parts']) else 1)" "$TMP/c.json" \
  && ok "a belyeg a SOROKBAN van, nem csak a jegyzetben" || no "a belyeg nem utazik a sorokkal"

# 3. a straight job says so explicitly, so a missing key later means something
python3 -c "
import json
json.dump({'job':'egyenes','w':1200,'h':800,'d':450,'t':18,
           'back':{'type':'applied','t':6}}, open('$TMP/straight.json','w'))"
python3 scripts/muhely/carcass.py "$TMP/straight.json" --cutlist "$TMP/s.json" >/dev/null 2>&1
[ "$(srcof "$TMP/s.json")" = "nincs ferde" ] \
  && ok "egyenes munka -> 'nincs ferde', nem hallgatas" || no "egyenes munka -> 'nincs ferde'"

# 4. no crying wolf: a hand-written straight list with no key stays silent
python3 -c "
import json
json.dump({'job':'kezzel irt','stock':{'w':2440,'h':1220},
 'parts':[{'name':'oldal','w':580,'h':720,'qty':2,'grain':'h','material':'18mm','edging':'2mm'},
          {'name':'polc','w':564,'h':575,'qty':2,'grain':'w','material':'18mm','edging':'2mm'}]},
 open('$TMP/hand.json','w'))"
python3 scripts/muhely/listcheck.py "$TMP/hand.json" >/dev/null 2>&1
[ $? -eq 0 ] && ok "kezzel irt egyenes lista -> nem kiabal" || no "hamis riasztas kezzel irt listan"

# 5. KNOWN LIMIT, asserted so it stays known. Neutral part names and no notes
#    hide a raked list from the text heuristic. This is why case 3 matters: the
#    guarantee is carcass writing the key, not this net catching everything.
python3 -c "
import json
json.dump({'job':'ferde, semleges nevekkel','stock':{'w':2440,'h':1220},
 'parts':[{'name':'A panel','w':400,'h':2000,'qty':1,'grain':'h','material':'18mm','edging':'2mm'},
          {'name':'B panel','w':400,'h':1316,'qty':1,'grain':'h','material':'18mm','edging':'2mm'},
          {'name':'C panel','w':1130.5,'h':400,'qty':1,'grain':'w','material':'18mm','edging':'2mm'}]},
 open('$TMP/neutral.json','w'))"
python3 scripts/muhely/listcheck.py "$TMP/neutral.json" >/dev/null 2>&1
[ $? -eq 0 ] && ok "ismert hatar: atnevezes elrejti a ferdet a heurisztika elol" \
             || no "a hatar megvaltozott -- ha ez SZANDEKOS javitas, ird at ezt a tesztet"

[ $fail -eq 0 ] && echo "  MIND RENDBEN" || echo "  BUKOTT"
exit $fail
