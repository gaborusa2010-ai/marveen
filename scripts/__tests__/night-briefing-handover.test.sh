#!/bin/bash
# A NIGHTBRIEF901 ag szarazprobaja: a briefing-kuldes logikaja, eles inditas nelkul.
#
# 2026-09-04 (kartya 6e07d9c2): EZ A TESZT KORABBAN NEM A SZKRIPTET FUTTATTA. Sajat
# run_branch() fuggvenyt definialt, ami ujraimplementalta a briefing-agat, es a
# scripts/agent-night-shift.sh fajlra sehol nem hivatkozott. Forgacs 2026-09-03-an
# atirta a szkriptet, a teszt 8/8 zoldet adott, es a nyolcbol az egyik NEVE eppen az
# volt, hogy "briefing nelkul nem csinal semmit" -- egy allitas, amit a valtoztatas
# ERVENYTELENITETT. A zold nem azt jelentette, hogy a kod jo; azt, hogy a MASODIK
# PELDANY valtozatlan.
#
# Mostantol a teszt SOURCE-olja a valodi szkriptet es a valodi send_night_briefing()
# fuggvenyt hivja. A szkript also fele BASH_SOURCE-guard mogott van, tehat a source
# nem indit el es nem allit le semmit -- API-hivas es token-olvasas nelkul fut le.
#
# AZ ELLENORZES, hogy a bekotes valodi (a kartya sajat mercéje): valtoztasd el a
# SZKRIPTET egy aprosaggal, es ennek PIROSNAK kell lennie. Ha zold marad, megint ket
# peldany van. Ezert nezi a teszt a valodi kimeneti szovegeket is, nem csak azt, hogy
# a fajl elmozdult-e: egy elnemitott ag ugyanugy nem mozgat fajlt, mint egy hibas.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${NIGHT_SHIFT_SCRIPT:-$ROOT/scripts/agent-night-shift.sh}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  PASS: $1"; }
no(){ FAIL=$((FAIL+1)); echo "  FAIL: $1"; }

# A VALODI szkript, source-olva. Ha ez barmit is CSINALNA (inditas, token-olvasas),
# az itt derulne ki: nincs dashboard-token es nincs argumentum a temp kornyezetben.
# shellcheck source=/dev/null
source "$SCRIPT"
if [ "$(type -t send_night_briefing)" != "function" ]; then
  echo "  FAIL: a source nem adta meg a send_night_briefing() fuggvenyt ($SCRIPT)"
  echo "Results: 0/1 passed"; exit 1
fi

# agent-msg.sh helyettesito: a $MSG_RESULT szerint viselkedik
cat > "$TMP/agent-msg.sh" <<'EOF'
#!/bin/bash
cat > /dev/null
case "${MSG_RESULT:-ok}" in
  ok)   echo "OK id=999"; exit 0 ;;
  fail) echo "FAIL 401 unauthorized"; exit 0 ;;   # a valodi csapda: exit 0, de nincs id
  err)  echo "curl: connection refused" >&2; exit 7 ;;
esac
EOF
chmod +x "$TMP/agent-msg.sh"

setup() { rm -rf "$TMP/base"; mkdir -p "$TMP/base/store" "$TMP/base/scripts"; cp "$TMP/agent-msg.sh" "$TMP/base/scripts/"; }

# stdout ES stderr egyben: a szkript a hiba-agakat stderr-re irja, es egy nem
# figyelt stderr pont a csendes bukast rejtene el.
run() { send_night_briefing "$TMP/base" anorak 2>&1; }

has() { # has <cimke> <minta>
  if grep -q -- "$2" "$TMP/out"; then ok "$1"; else no "$1 (a kimenetben nincs: $2)"; fi
}

echo "night-briefing (NIGHTBRIEF901) -- a VALODI szkript agat futtatva"
echo "==============================================================="
echo "  szkript: $SCRIPT"

setup; printf 'szoveg\n' > "$TMP/base/store/night-briefing-anorak.txt"
MSG_RESULT=ok run > "$TMP/out"
has "sikeres kuldes -> 'eligazitas kikuldve'" "eligazitas kikuldve"
[ -f "$TMP/base/store/night-briefing-anorak.txt" ] && no "sikeres kuldes utan a fajlnak el kell tunnie" || ok "sikeres kuldes utan a fajl nyugdijazva"
ls "$TMP/base/store/" | grep -q 'night-briefing-anorak.txt.sent-' && ok "a nyugdijazott fajl .sent- nevet kap" || no "hianyzik a .sent- masolat"

setup; printf 'szoveg\n' > "$TMP/base/store/night-briefing-anorak.txt"
MSG_RESULT=fail run > "$TMP/out"
has "FAIL valasz (exit 0!) -> 'NEM igazolt'" "eligazitas NEM igazolt"
[ -s "$TMP/base/store/night-briefing-anorak.txt" ] && ok "elutasitott kuldes utan a fajl MARAD (ujraprobalhato)" || no "elutasitott kuldesnel a fajlnak maradnia kell"

setup; printf 'szoveg\n' > "$TMP/base/store/night-briefing-anorak.txt"
MSG_RESULT=err run > "$TMP/out"
has "transzport-hiba -> 'kuldes HIBA'" "eligazitas kuldes HIBA"
[ -s "$TMP/base/store/night-briefing-anorak.txt" ] && ok "transzport-hiba utan a fajl MARAD" || no "transzport-hibanal a fajlnak maradnia kell"

# NIGHTBRIEF901-B (2026-09-03): a hianyzo eligazitas eddig NEMA volt, es a
# "nem volt mit mondani" ugyanugy nezett ki, mint a "senki nem irta meg".
# Anorak igy indult ures prompton. Mostantol jelzest kuld a fo-agensnek.
setup; MSG_RESULT=ok run > "$TMP/out"
has "briefing nelkul kimondja, hogy NINCS eligazitas-fajl" "NINCS eligazitas-fajl"
has "briefing nelkul JELEZ a fo-agensnek" "eligazitas-hiany jelezve"

setup; MSG_RESULT=fail run > "$TMP/out"
has "FAIL valasz (exit 0!) a jelzesnel -> 'NEM igazolt'" "jelzes NEM igazolt"

setup; MSG_RESULT=err run > "$TMP/out"
has "transzport-hiba a jelzesnel -> 'jelzes HIBA'" "jelzes HIBA"

setup; : > "$TMP/base/store/night-briefing-anorak.txt"
MSG_RESULT=ok run > "$TMP/out"
has "URES briefing-fajl is jelzest valt ki" "eligazitas-hiany jelezve"

# NIGHTALREADY179 (kartya 26a1eba2): ha az inditas "already running"-ot kapott,
# a hianyzo fajl NEM esemeny -- 2026-09-06 23:00-kor pont ez adott hamis riasztast.
setup; : > "$TMP/msg-called"; rm -f "$TMP/msg-called"
send_night_briefing "$TMP/base" anorak already > "$TMP/out" 2>&1
has "mar futo agensnel nem riaszt, kimondja miert" "mar futott az inditas elott"
grep -q "eligazitas-hiany jelezve" "$TMP/out" && no "mar futo agensnel NEM szabad jelezni" || ok "mar futo agensnel nincs jelzes"

# ...de egy LETEZO eligazitas a mar futo agensnek is kimegy.
setup; printf 'szoveg\n' > "$TMP/base/store/night-briefing-anorak.txt"
MSG_RESULT=ok send_night_briefing "$TMP/base" anorak already > "$TMP/out" 2>&1
has "mar futo agensnek a letezo eligazitas kimegy" "eligazitas kikuldve"

# A mai .sent- masolat: az eligazitas ma mar kiment, tehat nem "senki nem irta meg".
setup; : > "$TMP/base/store/night-briefing-anorak.txt.sent-$(date +%Y%m%d)-212715"
MSG_RESULT=ok run > "$TMP/out"
has "mai .sent- masolatnal nem riaszt" "mai eligazitas mar kiment"
grep -q "eligazitas-hiany jelezve" "$TMP/out" && no "mai .sent- mellett NEM szabad jelezni" || ok "mai .sent- mellett nincs jelzes"

# A TEGNAPI .sent- nem ment fel: az a mai eligazitas hianya.
setup; : > "$TMP/base/store/night-briefing-anorak.txt.sent-19990101-230000"
MSG_RESULT=ok run > "$TMP/out"
has "regi .sent- mellett tovabbra is jelez" "eligazitas-hiany jelezve"

# STANDING1010 (2026-10-10): eligazitas-fajl nelkul az ALLANDO eligazitas kimegy
# az agensnek, es MARAD (sosem nyugdijazodik); a fo-agens jelzese tovabbra is megy.
setup; printf 'allando\n' > "$TMP/base/store/standing-briefing-anorak.txt"
MSG_RESULT=ok run > "$TMP/out"
has "allando eligazitas kimegy, ha nincs napi fajl" "allando eligazitas kikuldve"
has "allando eligazitas mellett is jelez a fo-agensnek" "eligazitas-hiany jelezve"
[ -s "$TMP/base/store/standing-briefing-anorak.txt" ] && ok "az allando eligazitas fajlja MARAD" || no "az allando fajlnak maradnia kell"

setup; printf 'allando\n' > "$TMP/base/store/standing-briefing-anorak.txt"
MSG_RESULT=fail run > "$TMP/out"
has "allando eligazitas FAIL (exit 0!) -> 'NEM igazolt'" "allando eligazitas NEM igazolt"

setup; MSG_RESULT=ok run > "$TMP/out"
grep -q "allando eligazitas" "$TMP/out" && no "allando fajl nelkul nem szabad allando kuldest jelenteni" || ok "allando fajl nelkul nincs allando kuldes"

setup; printf 'napi\n' > "$TMP/base/store/night-briefing-anorak.txt"; printf 'allando\n' > "$TMP/base/store/standing-briefing-anorak.txt"
MSG_RESULT=ok run > "$TMP/out"
grep -q "allando eligazitas" "$TMP/out" && no "napi fajl mellett az allando NEM megy" || ok "napi fajl mellett az allando nem megy ki"

echo "==============================================================="
echo "Results: $PASS/$((PASS+FAIL)) passed"
[ "$FAIL" -gt 0 ] && exit 1
echo "All tests passed."
