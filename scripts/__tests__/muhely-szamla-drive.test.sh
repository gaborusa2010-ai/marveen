#!/usr/bin/env bash
# A szamla Drive-ra kerulese: a nev egy helyen szuletik, es a kapu tud NEMET mondani.
#
# 2026-09-17 reggel a gazda megkerdezte, miert nincsenek a szamlai a Drive-jan.
# Merve: a Szamlak mappaban a 10011 allt es semmi mas, kozben a 10012, 10013, 10014
# es 10015 08-30 ota a lemezen ult. Semmi nem romlott el. A szinkron-szkript egy
# KEZZEL irt listabol dolgozott, amibe egyetlen szamla neve volt beirva, es a szamla
# kiallitasa magatol nem toltott fel semmit. Egy lista, amit kezzel kell bovitenie
# valakinek, elavul -- es errol semmi nem szol.
#
# A teszt HALOZAT NELKUL fut: a Drive-hivasokat helyettesitjuk. Ket dolgot orzunk:
#
#   1. A LISTA A LEMEZROL jon, nem kezbol. Ha valaki visszair egy fix listat,
#      ez elbukik.
#   2. A KAPU TUD NEMET MONDANI, es a nem a HELYRE szol, nem egy env-valtozora.
#      A szamla-tesztek valodi PDF-et rajzolnak egy mktemp mappaba ($TMP/a.pdf);
#      e nelkul a kapu nelkul egy teszt-futas feltolt egy "a.pdf"-et a gazda
#      Szamlak mappajaba, kitalalt sorszam ala. Egy kapu, ami azon mulik, hogy a
#      hivo beallit-e egy valtozot, a valtozo kapuja, nem a mappae.
#   3. Es a POZITIV KONTROLL: ugyanaz a kapu a valodi szamla-utat AT is engedi.
#      Egy kapu, ami mindenre nemet mond, ugyanugy hasznalhatatlan.
set -u
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail=0
ok() { echo "  ok   $1"; }
no() { echo "  FAIL $1"; fail=1; }

M="$TMP/muhely"
mkdir -p "$M" "$TMP/store/muhely/invoices"
cp scripts/muhely/drive_invoices.py "$M/"
cp scripts/muhely/pdftext.py "$M/" 2>/dev/null || true

# Egy hamis drive_up, ami semmit nem hiv ki a halozatra, csak feljegyzi a szandekot.
cat > "$M/drive_up.py" <<'PY'
API = "https://example.invalid/files"
calls = []
class _G:
    def call(self, url, params=None, **kw):
        calls.append(("call", params))
        return {"files": [{"id": "x1", "name": "2026-08-24 Szamla 10011 - Picagne.pdf"}]}
g = _G()
def mkdir(name, parent=None):
    calls.append(("mkdir", name)); return ("id-" + name, False)
def put(path, parent=None, name=None):
    calls.append(("put", name)); return ("fid", True)
PY

py() { PYTHONPATH="$M" TMP="$TMP" python3 - "$@"; }

# --- 1. a lista a lemezrol jon ------------------------------------------------
: > "$TMP/store/muhely/invoices/10021-teszt-ugyfel.pdf"
: > "$TMP/store/muhely/invoices/10022-masik-ceg.pdf"
cat > "$TMP/store/muhely/invoices/10021-teszt-ugyfel.json" <<'JSON'
{"reference": "10021", "date": "2026-09-15"}
JSON
out=$(py <<'PY'
import os, sys
import drive_invoices as di
tmp = os.environ["TMP"]
di.INVOICE_DIR = os.path.join(tmp, "store", "muhely", "invoices")
for rel, name in di.entries(report=None):
    print(name)
PY
)
echo "$out" | grep -q "2026-09-15 Szamla 10021 - Teszt Ugyfel.pdf" \
  && ok "a sidecar datuma es az ugyfelnev a nevbe kerul" \
  || { no "a sidecar datuma/nev nem jo"; echo "$out" | sed 's/^/       /'; }
[ "$(echo "$out" | grep -c 'Szamla')" = "2" ] \
  && ok "MINDKET lemezen levo szamla szerepel (a lista nem kezbol jon)" \
  || { no "nem minden szamla szerepel"; echo "$out" | sed 's/^/       /'; }

# --- 2. a kapu NEMET mond a szamla-mappan kivulre -----------------------------
# A fajl LETEZZEN. Enelkul a kapu kivetele is "False"-t adna vissza (a megnyitas
# bukna el), es a negativ kontroll veletlenul menne at -- epp azt a hibat fedve el,
# amit ornie kell. A valodi szamla-tesztek is letezo PDF-et rajzolnak ide.
: > "$TMP/a.pdf"
out=$(py <<'PY'
import os, sys
import drive_invoices as di
tmp = os.environ["TMP"]
di.INVOICE_DIR = os.path.join(tmp, "store", "muhely", "invoices")
msgs = []
r = di.upload_one(os.path.join(tmp, "a.pdf"), report=msgs.append)
import drive_up
print("ret:", r)
print("hivasok:", len(drive_up.calls))
print("uzenet:", msgs[0] if msgs else "")
PY
)
echo "$out" | grep -q "ret: False" && ok "idegen utvonalra NEM tolt fel" || { no "idegen utvonalat feltoltott"; echo "$out"|sed 's/^/       /'; }
echo "$out" | grep -q "hivasok: 0" && ok "es el sem indult a Drive-hivas" || { no "elindult a Drive-hivas"; echo "$out"|sed 's/^/       /'; }

# --- 3. POZITIV KONTROLL: a valodi szamla-utat ATENGEDI -----------------------
out=$(py <<'PY'
import os, sys
import drive_invoices as di
tmp = os.environ["TMP"]
di.INVOICE_DIR = os.path.join(tmp, "store", "muhely", "invoices")
msgs = []
r = di.upload_one(os.path.join(di.INVOICE_DIR, "10021-teszt-ugyfel.pdf"),
                  report=msgs.append)
import drive_up
print("ret:", r)
print("put:", [c[1] for c in drive_up.calls if c[0] == "put"])
PY
)
echo "$out" | grep -q "ret: True" && ok "a valodi szamla-utat ATENGEDI (pozitiv kontroll)" || { no "a valodi utat is elutasitotta"; echo "$out"|sed 's/^/       /'; }
echo "$out" | grep -q "2026-09-15 Szamla 10021" && ok "es a helyes nevvel tolti fel" || { no "rossz nevvel toltene fel"; echo "$out"|sed 's/^/       /'; }

# --- 4. MUHELY_NO_DRIVE kikapcsolja ------------------------------------------
out=$(MUHELY_NO_DRIVE=1 py <<'PY'
import os
import drive_invoices as di
tmp = os.environ["TMP"]
di.INVOICE_DIR = os.path.join(tmp, "store", "muhely", "invoices")
print("ret:", di.upload_one(os.path.join(di.INVOICE_DIR, "10021-teszt-ugyfel.pdf"), report=None))
PY
)
echo "$out" | grep -q "ret: False" && ok "MUHELY_NO_DRIVE=1 kikapcsolja" || { no "MUHELY_NO_DRIVE nem hat"; echo "$out"|sed 's/^/       /'; }

# --- 5. ugyanaz a sorszam mas neven: FRISSIT, nem duplikal --------------------
out=$(py <<'PY'
import drive_invoices as di
files = [("invoices/10011-picagne.pdf", "2026-08-25 Szamla 10011 - Picagne.pdf")]
got = di.keep_existing_names(files, "folder-id", report=None)
print("nev:", got[0][1])
PY
)
echo "$out" | grep -q "nev: 2026-08-24 Szamla 10011 - Picagne.pdf" \
  && ok "a mar fent levo 10011 nevet tartja meg (nem duplikal)" \
  || { no "uj nevet adna ugyanannak a sorszamnak -> duplikatum"; echo "$out"|sed 's/^/       /'; }

# A 10011 eltereset a gazda eldontotte ("Maradhat.", 2026-09-17), ezert az NEM szol
# tobbe. A nemitas viszont SZOLJON egy ELDONTETLEN elteresre, kulonben egy kesobbi,
# valodi utkozes is csendben elsullyedne -- ezert a nemitas ket iranyat kulon merjuk.
out=$(py <<'PYX'
import drive_invoices as di
msgs = []
di.keep_existing_names([('x', '2026-08-25 Szamla 10011 - Picagne.pdf')], 'f', msgs.append)
print('10011 uzenetek:', len(msgs))
di.ELFOGADOTT_NEVELTERES = {}
di.keep_existing_names([('x', '2026-08-25 Szamla 10011 - Picagne.pdf')], 'f', msgs.append)
print('eldontetlen uzenetek:', len(msgs))
PYX
)
echo "$out" | grep -q "10011 uzenetek: 0" \
  && ok "az ELDONTOTT elteres nem szol tobbe" \
  || { no "az eldontott elteres meg mindig riaszt"; echo "$out"|sed 's/^/       /'; }
echo "$out" | grep -q "eldontetlen uzenetek: 1" \
  && ok "de egy ELDONTETLEN elteres IGEN (a nemitas nem altalanos)" \
  || { no "a nemitas egy eldontetlen elterest is elnyelt"; echo "$out"|sed 's/^/       /'; }

# --- 6. arajanlatot nem archival ---------------------------------------------
out=$(python3 - <<'PY'
import sys
sys.path.insert(0, "scripts/muhely")
import invoice
sent = []
class Fake:
    @staticmethod
    def upload_one(p, report=None): sent.append(p); return True
sys.modules["drive_invoices"] = Fake
invoice.archive_to_drive("quote", "store/muhely/invoices/10011-picagne.pdf")
print("arajanlat utan:", len(sent))
invoice.archive_to_drive("invoice", "store/muhely/invoices/10011-picagne.pdf")
print("szamla utan:", len(sent))
PY
)
echo "$out" | grep -q "arajanlat utan: 0" && ok "arajanlatot NEM archival" || { no "arajanlatot is archivalna"; echo "$out"|sed 's/^/       /'; }
echo "$out" | grep -q "szamla utan: 1" && ok "szamlat IGEN (pozitiv kontroll)" || { no "szamlat sem archival"; echo "$out"|sed 's/^/       /'; }

[ $fail -eq 0 ] && echo "MIND OK" || echo "VOLT BUKAS"
exit $fail
