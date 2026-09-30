#!/usr/bin/env bash
# The overtime figure is HIS word for one shape of day, and a DERIVATION for two
# others. This fires on those two, and must stay silent on every shape he priced.
#
# His words cover a nine-hour day of presence: 200, and 25/hour past it
# (2026-09-09, 2026-09-10). Never put to him: a shift crossing midnight, and a
# day starting before 07:00. Those two sat on the weekend list for three weeks
# labelled "not urgent" -- while the true label was that there was NO CASE:
# measured 2026-09-12 across all 17 jobs, zero of each. So the question belongs
# at the moment it first matters, not on a list.
#
# BOTH DIRECTIONS COST. An alert that fires on ordinary days becomes noise inside
# a fortnight, and the real one gets paged past with it. The 07:00-18:00 day is
# the guard on that side: it carries two hours of overtime AND is his own worked
# example, so it must stay silent.
set -u
cd "$(dirname "$0")/../.." || exit 1
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

# $1 = start "YYYY-MM-DD HH:MM", $2 = stop, prints "FIRES" or "SILENT"
note() {
  python3 - "$1" "$2" <<'PY'
import sys, datetime, importlib.util
spec = importlib.util.spec_from_file_location('jobs', 'scripts/muhely/jobs.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
ts = lambda s: datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp()
r = {"started_at": ts(sys.argv[1]), "stopped_at": ts(sys.argv[2])}
n = m._derived_rule_note(r)
print("FIRES" if n else "SILENT")
if n and "LEVEZETES" not in n:
    print("NO_PROVENANCE")
if n and "?" in n:
    print("ASKS_A_QUESTION")
PY
}

echo "muhely-levezetes-ebreszto"

# 1. The two shapes he never priced MUST fire.
for c in "2026-09-12 06:00|2026-09-12 16:00|korai kezdes" \
         "2026-09-12 05:30|2026-09-12 14:30|nagyon korai" \
         "2026-09-12 22:00|2026-09-13 09:00|ejszakai muszak" \
         "2026-09-12 20:00|2026-09-13 06:00|estetol reggelig"; do
  IFS='|' read -r s e label <<< "$c"
  out=$(note "$s" "$e")
  case "$out" in
    FIRES*) ok "megszolal: $label" ;;
    *)      no "NEM szolalt meg: $label" ;;
  esac
done

# 2. Every shape he DID price must stay silent -- including the long day, which
#    carries overtime but is his own example (07:00-18:00 = 250).
for c in "2026-09-12 07:00|2026-09-12 16:00|rendes muhelynap" \
         "2026-09-12 08:00|2026-09-12 17:00|rendes helyszini nap" \
         "2026-09-12 07:00|2026-09-12 18:00|az O sajat 250-es peldaja" \
         "2026-09-12 07:00|2026-09-12 12:00|fel nap"; do
  IFS='|' read -r s e label <<< "$c"
  out=$(note "$s" "$e")
  case "$out" in
    SILENT*) ok "hallgat: $label" ;;
    *)       no "HAMIS RIASZTAS: $label" ;;
  esac
done

# 3. The wording must state provenance and must NOT ask a question.
out=$(note "2026-09-12 06:00" "2026-09-12 16:00")
case "$out" in
  *NO_PROVENANCE*)   no "a szoveg nem mondja ki, hogy LEVEZETES" ;;
  *ASKS_A_QUESTION*) no "a szoveg kerdez, pedig nem szabad" ;;
  *)                 ok "a szoveg levezetesnek nevezi es nem kerdez" ;;
esac

# 4. It must not fire on a job that has not finished yet.
python3 - <<'PY' && ok "futo munkara hallgat" || no "futo munkara elsult"
import importlib.util
spec = importlib.util.spec_from_file_location('jobs', 'scripts/muhely/jobs.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
raise SystemExit(1 if m._derived_rule_note({"started_at": 1789106400, "stopped_at": None}) else 0)
PY

# 5. ZERO NOISE ON THE REAL LEDGER: it must not fire on any closed job today.
#    This case is about the owner's DATA, not the code, so it runs only where that
#    data is: on a tree without store/muhely-jobs.json (a fresh clone, a worktree)
#    it used to die with a FileNotFoundError traceback. There it is a stated SKIP,
#    never a silent pass -- cases 1-4 above carry the code contract everywhere.
if [ ! -f store/muhely-jobs.json ]; then
  printf '  SKIP az elo naplo (store/muhely-jobs.json) ezen a fan nincs, az 5. eset nem futott\n'
  exit $fail
fi
python3 - <<'PY'
import json, importlib.util
spec = importlib.util.spec_from_file_location('jobs', 'scripts/muhely/jobs.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
d = json.load(open('store/muhely-jobs.json'))
rows = d if isinstance(d, list) else d.get('jobs', [])
fired = [r['id'] for r in rows if m._derived_rule_note(r)]
print(f"  {'ok  ' if not fired else 'FAIL'} az elo naplo {len(rows)} munkajabol elsul: {len(fired)}")
raise SystemExit(1 if fired else 0)
PY
[ $? -eq 0 ] || fail=1

exit $fail
