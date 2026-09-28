#!/usr/bin/env bash
# naplo-hiany.py: tud-e MINDKET valaszt adni, es a nyugtazas tenyleg elnemitja-e.
# Izolalt adaton fut (MUHELY_JOBS / MUHELY_NAPLO_HIANY_SEEN), az eles naplohoz nem nyul.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$REPO/scripts/muhely/naplo-hiany.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export MUHELY_JOBS="$TMP/jobs.json"
export MUHELY_NAPLO_HIANY_SEEN="$TMP/seen.json"

fail=0
ok()   { printf 'ok   %s\n' "$1"; }
bad()  { printf 'FAIL %s\n' "$1"; fail=1; }

# A fixture-t MENETKOZBEN generaljuk a mai naphoz kepest, kulonben a teszt egy het
# mulva mast merne. A tegnapi es tegnapelotti munkanapot toltjuk fel.
mkfix() {  # $1 = hany elozo MUNKANAPRA legyen sor (0 = egyikre sem)
  python3 - "$1" <<'PY'
import json,os,sys
from datetime import datetime,timedelta
n=int(sys.argv[1]); ma=datetime.now().astimezone().date()
rows=[];i=1;tett=0
while tett<n and i<20:
    d=ma-timedelta(days=i); i+=1
    if d.weekday()==6: continue
    ts=datetime(d.year,d.month,d.day,7,0).astimezone().timestamp()
    rows.append({"id":100+tett,"client":"T","job":"T","started_at":ts,
                 "stopped_at":ts+9*3600,"rate":200.0})
    tett+=1
json.dump(rows,open(os.environ["MUHELY_JOBS"],"w"))
PY
}

# 1. IGEN-ag: minden munkanapra van sor -> exit 0
mkfix 7
out="$(python3 "$SCRIPT" --napok 5 2>&1)"; rc=$?
[ "$rc" = 0 ] && ok "teli naplo -> exit 0" || bad "teli naplo -> exit $rc ($out)"

# 2. NEM-ag: egyetlen sor sincs -> exit 1, es nevezze meg a tegnapi munkanapot
mkfix 0
out="$(python3 "$SCRIPT" --napok 5 2>&1)"; rc=$?
[ "$rc" = 1 ] && ok "ures naplo -> exit 1" || bad "ures naplo -> exit $rc"
echo "$out" | grep -q "NINCS NAPLO-SOR" && ok "ures naplo -> megnevezi a hianyt" \
  || bad "ures naplo -> nem irta ki a hianyt ($out)"

# 3. A nyugtazas elnemit: nyugtazzuk MINDEN kiirt napot, utana exit 0
napok="$(echo "$out" | sed -n 's/^  \([0-9-]\{10\}\) .*/\1/p')"
[ -n "$napok" ] || bad "nem tudtam kiolvasni a hianyzo napokat"
for d in $napok; do python3 "$SCRIPT" --nyugtaz "$d" --ok "teszt" >/dev/null; done
python3 "$SCRIPT" --napok 5 >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && ok "nyugtazas utan -> exit 0" || bad "nyugtazas utan -> exit $rc"

# 4. A nyitott rekordot a hianyzo napoktol FUGGETLENUL jelzi
python3 - <<'PY'
import json,os
from datetime import datetime,timedelta
ts=(datetime.now().astimezone()-timedelta(hours=30)).timestamp()
rows=json.load(open(os.environ["MUHELY_JOBS"]))
rows.append({"id":999,"client":"T","job":"T","started_at":ts,"stopped_at":None,"rate":200.0})
json.dump(rows,open(os.environ["MUHELY_JOBS"],"w"))
PY
out="$(python3 "$SCRIPT" --napok 5 2>&1)"; rc=$?
[ "$rc" = 1 ] && ok "30 oraja nyitott rekord -> exit 1" || bad "nyitott rekord -> exit $rc"
echo "$out" | grep -q "NYITVA MARADT: #999" && ok "nyitott rekord -> nevesiti" \
  || bad "nyitott rekord -> nem nevesiti ($out)"

# 5. Vasarnapra SOHA ne kerdezzen.
# FRISS seen-fajl kell: a 3. lepes nyugtazasai kulonben elnemitanak egy valodi bukast.
# Merve a sajat negativ kontrollomon (2026-09-16): a vasarnap-kihagyast kikapcsolva ez az
# eset MEGIS zoldet adott, mert a 3. lepes mar lenyugtazta a vasarnapot is.
#
# ES A MASODIK, SULYOSABB HIBA UGYANITT: az ellenorzes eloszor igy allt:
#     python3 "$SCRIPT" ... | python3 - <<'PY'
# A heredoc a MASODIK python stdinje, tehat FELULIRJA a cso adatat: a szuro egy ures
# stdint olvasott, es ezert MINDIG zoldet adott. Egy ellenorzes, ami nem tud nemet
# mondani, nem ellenorzes. Ezert megy a datum-lista ARGUMENTUMBAN, nem csovon.
rm -f "$MUHELY_NAPLO_HIANY_SEEN"
mkfix 0
out="$(python3 "$SCRIPT" --napok 10 2>&1)"
napok="$(echo "$out" | sed -n 's/^  \([0-9-]\{10\}\) .*/\1/p')"
[ -n "$napok" ] || bad "5. eset: nem kaptam egyetlen hianyzo napot sem, nincs mit merni"
# shellcheck disable=SC2086
if python3 -c '
import sys
from datetime import datetime
rossz=[d for d in sys.argv[1:] if datetime.strptime(d,"%Y-%m-%d").weekday()==6]
sys.exit(1 if rossz else 0)
' $napok; then
  ok "vasarnapot soha nem kerdez"
else
  bad "vasarnapot kerdezett: $(echo $napok | tr "\n" " ")"
fi

# 6. EMBER MOD: a kimenetben SOHA nincs parancs-sor.
# Ez SZERZODES, nem kozmetika. 2026-09-16-an a gep-olvashato kimenet valtozatlanul kiment
# a gazdanak Telegramra, benne a "--nyugtaz" parancs-sorral. A hivo utolag kiszedte azt a
# sort, de egy SZOVEGRE epulo szuro nemán atenged, amint a sor megfogalmazasa valtozik.
rm -f "$MUHELY_NAPLO_HIANY_SEEN"
mkfix 0
out="$(python3 "$SCRIPT" --napok 5 --ember 2>&1)"; rc=$?
[ "$rc" = 1 ] && ok "ember mod -> exit 1" || bad "ember mod -> exit $rc"
if echo "$out" | grep -q -e "--nyugtaz" -e "python3" -e "naplo-hiany.py"; then
  bad "ember mod parancsot tartalmaz: $out"
else
  ok "ember mod -> nincs benne parancs"
fi
echo "$out" | grep -q "Dolgoztal aznap?\|Dolgozt.l aznap?" && ok "ember mod -> ember-mondat" \
  || bad "ember mod -> nem ember-mondat ($out)"

# 7. Az ember-kimenet MENJEN AT a kimeno kapun. A gazdanak megy, tehat ugyanaz a merce
# all ra, mint barmely kikuldott szovegre (gondolatjel, kotojeles toldalek, hossz).
GATE="$REPO/scripts/muhely/gate-check.py"
if [ -f "$GATE" ]; then
  printf '%s\n' "$out" > "$TMP/ember.txt"
  if python3 "$GATE" "$TMP/ember.txt" >/dev/null 2>&1; then
    ok "ember mod -> atmegy a gate-check-en"
  else
    bad "ember mod -> a gate-check megfogja: $(python3 "$GATE" "$TMP/ember.txt" 2>&1 | tail -1)"
  fi
fi

# 8. Ember modban a NYITOTT rekord is ember-nyelven all
python3 -c "
import json,os
from datetime import datetime,timedelta
ts=(datetime.now().astimezone()-timedelta(hours=30)).timestamp()
rows=json.load(open(os.environ['MUHELY_JOBS']))
rows.append({'id':777,'client':'T','job':'T','started_at':ts,'stopped_at':None,'rate':200.0})
json.dump(rows,open(os.environ['MUHELY_JOBS'],'w'))
"
out="$(python3 "$SCRIPT" --napok 5 --ember 2>&1)"
echo "$out" | grep -q "#777 munkanapl" && ok "ember mod -> nyitott rekord ember-nyelven" \
  || bad "ember mod -> a nyitott rekord nem ember-nyelven all ($out)"

# 9. AZ ALAPERTELMEZETT ABLAK fogja-e a 8 napnal regebbi hianyt.
# Ez a buktato eloszor elesben jelentkezett: a het napos alapertelmezes URES kimenetet adott
# egy tizenegy napos hianyra, ugyanarra a kerdesre, amire a --napok 14 valaszt adott. Az
# ablakot azert kell KAPCSOLO NELKUL merni, mert a hivo az alapertelmezest fogja hasznalni.
rm -f "$MUHELY_NAPLO_HIANY_SEEN"
mkfix 0
regi_nap="$(python3 -c "
from datetime import date,timedelta
d=date.today()-timedelta(days=10)
while d.weekday()==6: d-=timedelta(days=1)
print(d.isoformat())
")"
out="$(python3 "$SCRIPT" 2>&1)"
echo "$out" | grep -q "$regi_nap" && ok "alapertelmezett ablak -> a 10 napos hianyt is fogja" \
  || bad "alapertelmezett ablak nem fogja a 10 napos hianyt ($regi_nap): $out"

[ "$fail" = 0 ] && echo "MIND OK" || echo "VOLT BUKO"
exit "$fail"
