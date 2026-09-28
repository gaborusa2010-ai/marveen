#!/usr/bin/env bash
# The 42 mm undermount deduction is BLUM TANDEM's number, and this shop is not Blum.
#
# The owner photographed his runner on 2026-08-27: a GRASS Dynapro
# (F130 107 853 201, VA NL650 70 3D). Grass works to a different deduction, and
# the exact figure is in the fitting leaflet in the box, not measured yet. So for
# THIS workshop the default is not a neutral default: it is the wrong maker's
# number, and it sizes a drawer box that must fit a real runner.
#
# carcass.py must say so whenever a job leans on the default, and must stay quiet
# when the owner has decided the number himself or the runner is not undermount.
# A checker that also fires on his own explicit value would be ignored inside a week.
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

spec() {  # $1 = the drawers group as JSON
  python3 - "$1" "$TMP/spec.json" <<'PY'
import json, sys
dg, out = json.loads(sys.argv[1]), sys.argv[2]
json.dump({"job": "teszt", "w": 900, "h": 700, "d": 500, "t": 18,
           "back": {"type": "applied", "t": 6}, "drawers": [dg]}, open(out, "w"))
PY
}

# The run must SUCCEED before its text means anything: a crash piped into grep
# looks exactly like "the warning did not fire". That mistake was made while
# writing this very check, on 2026-09-09, with a mis-shaped spec.
run() {
  OUT=$(python3 scripts/muhely/carcass.py "$TMP/spec.json" 2>&1); RC=$?
  [ "$RC" -eq 0 ] || { no "a futas elhalt (exit $RC): $(printf '%s' "$OUT" | tail -1)"; return 1; }
  return 0
}
warns() { printf '%s' "$OUT" | grep -qi "alapertelmezett 42"; }

echo "muhely-alulszerelt-sin-gyarto"

# 1. undermount, no explicit deduction -> the default is Blum's, so warn
spec '{"count":2,"front_h":150,"runner":"under","bottom_t":6}'
run && { warns && ok "alapertelmezett levonas -> figyelmeztet" \
                 || no "alapertelmezett levonas -> NEM figyelmeztet"; }

# 2. the owner gave the number himself -> his decision, stay quiet
spec '{"count":2,"front_h":150,"runner":"under","bottom_t":6,"inside_deduction":49}'
run && { warns && no "explicit inside_deduction -> hamis riasztas" \
                 || ok "explicit inside_deduction -> hallgat"; }

# 3. side runner: the deduction does not apply at all
spec '{"count":2,"front_h":150,"runner":"side","bottom_t":6,"runner_length":450}'
run && { warns && no "oldalsin -> hamis riasztas" \
                 || ok "oldalsin -> hallgat"; }

# 4-6. A WARNING THE HANDED-ON FILE FORGETS is a warning for whoever ran the
# command, not for whoever cuts the sheet. Measured 2026-09-18: the FIGYELEM
# printed on screen and the --cutlist JSON held no trace of it -- not the word,
# not the number, not the maker. carcass.py now writes the job-level notes into
# that file, the same rule its own comment has applied to per-part notes since
# day one.
cut() {  # writes $TMP/cut.json, leaves the text output in $OUT
  OUT=$(python3 scripts/muhely/carcass.py "$TMP/spec.json" --cutlist "$TMP/cut.json" 2>&1); RC=$?
  [ "$RC" -eq 0 ] || { no "a --cutlist futas elhalt (exit $RC): $(printf '%s' "$OUT" | tail -1)"; return 1; }
  return 0
}
injson() { grep -qi "$1" "$TMP/cut.json"; }

spec '{"count":2,"front_h":150,"runner":"under","bottom_t":6}'
if cut; then
  injson "alapertelmezett 42" && ok "a FIGYELEM atmegy a cutlist JSON-ba" \
                             || no "a FIGYELEM NEM megy at a cutlist JSON-ba"
  # NEGATIV KONTROLL a mereshez magahoz: ha a grep barmire illeszkedne, a fenti
  # sor akkor is zold lenne, amikor a mezo ures. Egy szonak HIANYOZNIA is kell.
  injson "ez a szo nem all a fajlban sehol" && no "a grep mindenre illeszkedik, a 4. eset ertektelen" \
                                           || ok "negativ kontroll: a grep tud nemet mondani"
fi

# 6. Side runner: nothing about the undermount deduction may reach the file.
spec '{"count":2,"front_h":150,"runner":"side","bottom_t":6,"runner_length":450}'
cut && { injson "alapertelmezett 42" && no "oldalsin -> a cutlist JSON-ban is hamis riasztas" \
                                     || ok "oldalsin -> a cutlist JSON tiszta"; }

# 7. The maker rides ALONG with the figure. A bare 49 reads the same as a guess
# six months on; runner_brand is what tells the next person whose leaflet to open.
spec '{"count":2,"front_h":150,"runner":"under","bottom_t":6,"inside_deduction":49,"runner_brand":"Grass Dynapro"}'
cut && { injson "Grass Dynapro" && ok "a runner_brand atmegy a cutlist JSON-ba" \
                                || no "a runner_brand NEM megy at a cutlist JSON-ba"; }

exit $fail
