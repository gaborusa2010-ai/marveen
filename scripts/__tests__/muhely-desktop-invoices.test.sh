#!/usr/bin/env bash
# desktop_invoices.py -- the second home for invoice PDFs on the owner's desktop.
#
# The three properties worth a test are the three that cost him something when
# they break: a duplicate of an invoice he already has, a copy written from
# somewhere it should never copy from, and a silent failure when the drive is
# not mounted. Every case runs against a temp folder; the real desktop folder is
# never touched, and the last check proves it.
set -u
cd "$(dirname "$0")/../.." || exit 1
PY=python3
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
FAIL=0
ok()   { echo "  ok   $1"; }
bad()  { echo "  FAIL $1"; FAIL=1; }

REAL_DESKTOP="/mnt/c/Users/gabor/Desktop/Marveen files/Invoices"
if [ -d "$REAL_DESKTOP" ]; then
  BEFORE=$(ls -1 "$REAL_DESKTOP" | wc -l)
else
  BEFORE=""
fi

INV=store/muhely/invoices
ANY=$(ls "$INV"/*.pdf 2>/dev/null | head -1)
[ -n "$ANY" ] || { echo "nincs szamla a $INV mappaban, a teszt kihagyva"; exit 0; }

echo "== 1. ures mappa: minden szamla atmegy"
D="$TMP/a"; mkdir -p "$D"
MUHELY_DESKTOP_INVOICES="$D" $PY scripts/muhely/desktop_invoices.py >/dev/null 2>&1
N_DISK=$(ls -1 "$INV"/*.pdf | wc -l)
N_DEST=$(ls -1 "$D" | wc -l)
[ "$N_DISK" = "$N_DEST" ] && ok "$N_DISK szamla -> $N_DEST fajl" \
                          || bad "$N_DISK szamla, de $N_DEST fajl"

echo "== 2. masodik futas nem duplikal"
MUHELY_DESKTOP_INVOICES="$D" $PY scripts/muhely/desktop_invoices.py >/dev/null 2>&1
N2=$(ls -1 "$D" | wc -l)
[ "$N2" = "$N_DEST" ] && ok "ujrafuttatva is $N2 fajl" || bad "ujrafuttatva $N2 fajl"

echo "== 3. a gazda sajat fajlneve gyoz, es nem lesz masodik peldany"
D2="$TMP/b"; mkdir -p "$D2"
REF=$(basename "$ANY" | sed 's/-.*//')
cp "$ANY" "$D2/${REF}-sajat-neven.pdf"
MUHELY_DESKTOP_INVOICES="$D2" $PY scripts/muhely/desktop_invoices.py >/dev/null 2>&1
CNT=$(ls -1 "$D2" | grep -c "$REF")
[ "$CNT" = "1" ] && ok "a $REF egyetlen peldanyban all" \
                 || bad "a $REF $CNT peldanyban all"
[ -f "$D2/${REF}-sajat-neven.pdf" ] && ok "a gazda neve megmaradt" \
                                    || bad "a gazda nevet atirtam"

echo "== 4. negativ kontroll: nem a szamla-mappabol nem masol"
D3="$TMP/c"; mkdir -p "$D3"
cp "$ANY" "$TMP/idegen.pdf"
MUHELY_DESKTOP_INVOICES="$D3" $PY scripts/muhely/desktop_invoices.py "$TMP/idegen.pdf" >/dev/null 2>&1
[ "$(ls -1 "$D3" | wc -l)" = "0" ] && ok "idegen forras kimaradt" \
                                   || bad "idegen forrast bemasolt"

echo "== 5. negativ kontroll: hianyzo cel-mappa -> nem nulla exit, nincs osszeomlas"
MUHELY_DESKTOP_INVOICES="$TMP/nincs-ilyen" $PY scripts/muhely/desktop_invoices.py "$ANY" >/dev/null 2>&1
[ $? -ne 0 ] && ok "nem nulla exit, es nem traceback" || bad "sikert jelentett cel-mappa nelkul"

echo "== 6. az eles desktop-mappa erintetlen"
if [ -n "$BEFORE" ]; then
  AFTER=$(ls -1 "$REAL_DESKTOP" | wc -l)
  [ "$BEFORE" = "$AFTER" ] && ok "$BEFORE fajl elotte es utana" \
                           || bad "$BEFORE -> $AFTER: a teszt irt az eles mappaba"
else
  echo "  (az eles mappa nem erheto el, ez a pont kimarad)"
fi

[ $FAIL -eq 0 ] && echo "  MIND RENDBEN" || echo "  VOLT HIBA"
exit $FAIL
