#!/usr/bin/env bash
# A guard that can only ever say "I cannot tell" is not a guard.
#
# jobs.py refuses to edit a job's description once an invoice has gone out, so
# the paper in the customer's hand and the record here cannot drift apart. It
# had no way to know one had gone out, so it asked for --force on every closed
# job. This log is the fact it was missing: which job each issued reference was
# spent on.
#
# The assertions are mostly about WHEN it may be written. Writing it a moment
# too early would be worse than not writing it at all -- a failed render would
# register an invoice nobody ever sent, and the guard would then block a
# correction citing that phantom document.
#
# Everything runs against temp paths. A test that spends a real invoice number
# would leave a hole in the owner's sequence, and to an accountant a missing
# number looks like a missing invoice.
set -u
cd "$(dirname "$0")/../.." || exit 1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
no() { printf '  FAIL %s\n' "$1"; fail=1; }

echo "muhely-invoice-log"

COUNTERS_BEFORE=$(cat store/muhely-counters.json 2>/dev/null)

TMP="$TMP" python3 - <<'PY'
import sys, os, json
sys.path.insert(0, "scripts/muhely")
import invoice as inv

tmp = os.environ["TMP"]
inv.ISSUED   = os.path.join(tmp, "log.json")
inv.COUNTERS = os.path.join(tmp, "counters.json")
inv.STORE    = tmp

business = json.load(open("store/muhely-business.json"))
def job(job_id):
    j = {"client": {"name": "Palmer Furniture", "address": ["X"]},
         "job": "TESZT", "cis": False,
         "lines": [{"desc": "Labour", "qty": 1, "unit": 100.0}], "notes": ""}
    if job_id is not None:
        j["_job_id"] = job_id
    return j

res = []
def check(label, cond):
    res.append((label, bool(cond)))

r1 = inv.render("invoice", job(5), business, os.path.join(tmp, "a.pdf"))
rows = inv._issued_load()
check("a kiadott sorszam bekerul a nyilvantartasba", len(rows) == 1)
check("a bejegyzes a munkahoz kotodik", rows[0]["job_id"] == 5)
check("issued_for megtalalja a munkat", [x["reference"] for x in inv.issued_for(5)] == [r1["reference"]])
check("mas munkara nem ad talalatot", inv.issued_for(4) == [])

# Re-rendering the same reference is one document, not two.
j2 = job(5); j2["reference"] = r1["reference"]
inv.render("invoice", j2, business, os.path.join(tmp, "b.pdf"))
check("ugyanaz a hivatkozas ujrarenderelve nem lesz ket bejegyzes",
      len(inv._issued_load()) == 1)

# A hand-written job file cannot be vouched for, and must not pretend otherwise.
inv.render("invoice", job(None), business, os.path.join(tmp, "c.pdf"))
rows = inv._issued_load()
check("kezzel irt JSON eseten a job_id None", rows[-1]["job_id"] is None)
check("issued_for(None) ures marad -- itt a --force kerdes helyben van",
      inv.issued_for(None) == [])

# A quote is not a bill. The kind is recorded so nothing downstream can read
# one as the other.
inv.render("quote", job(7), business, os.path.join(tmp, "d.pdf"))
q = [r for r in inv._issued_load() if r["job_id"] == 7]
check("az arajanlat kulon kind-dal kerul be", q and q[0]["kind"] == "quote")

# Writing the log must never take the invoice down with it: the PDF exists and
# the number is already spent, so refusing here would help nobody.
inv.ISSUED = "/proc/nincs-ilyen-konyvtar/log.json"
try:
    r = inv.render("invoice", job(9), business, os.path.join(tmp, "e.pdf"))
    check("irhatatlan naplo eseten a szamla ELKESZUL", os.path.exists(os.path.join(tmp, "e.pdf")))
    check("es kap sorszamot", bool(r.get("reference")))
except Exception as e:
    check(f"irhatatlan naplo NEM dobhat kivetelt (dobott: {type(e).__name__})", False)

for label, good in res:
    print(("ok   " if good else "FAIL ") + label)
sys.exit(0 if all(g for _, g in res) else 1)
PY
py_status=$?
[ $py_status -eq 0 ] || fail=1

COUNTERS_AFTER=$(cat store/muhely-counters.json 2>/dev/null)
[ "$COUNTERS_BEFORE" = "$COUNTERS_AFTER" ] \
  && ok "a VALODI sorszamlalo erintetlen maradt" \
  || no "a teszt elhasznalt egy eles szamlaszamot"

[ ! -f store/muhely-invoice-log.json ] || [ -n "${ALLOW_REAL_LOG:-}" ] \
  && ok "a teszt nem irt az eles nyilvantartasba" \
  || no "a teszt az eles nyilvantartasba irt"

[ $fail -eq 0 ] && echo "  MIND RENDBEN" || echo "  BUKOTT"
exit $fail
