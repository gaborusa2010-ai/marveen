#!/usr/bin/env bash
# agent-msg.sh -- reliable inter-agent message send for the Marveen fleet.
#
# WHY: the common `curl -s ... >/dev/null && echo sent` pattern is DANGEROUS -- curl exits 0 even when
# the server REJECTED the request (401/400/5xx), producing a SILENT send failure: the recipient never
# gets the message and two agents can wait on each other forever. The /api/messages router itself is
# fine (HTTP 200 + a message id); the bug is that the SENDER never checks the result. This helper checks
# the HTTP status AND the returned message id, and RETRIES on failure. A message counts as sent only
# when an id came back.
#
# Usage:  bash scripts/agent-msg.sh <from> <to> "<content>"
#   content: plain text (quotes / newlines OK) -- the body is built with json.dumps (no quoting pitfalls).
#   large / multi-line content may come from STDIN when the 3rd arg is "-":
#     echo "<long text>" | bash scripts/agent-msg.sh <from> <to> -
# Output: success -> "OK id=<n>"; failure -> "FAIL <reason>" + a line in store/agent-msg-failures.log, exit 1.
# Env: MARVEEN_WEB_PORT (default 3420).
#
# CLOSING A ROW IN THE SAME BREATH -- and why it is a flag and not a second command:
#   bash scripts/agent-msg.sh --close 4500 --result "Kesz. Reszletek: msg {id}" <from> <to> -
# The literal {id} is replaced with the id THIS send actually got back. Doubling it -- {{id}} --
# means the placeholder ITSELF, and comes out as a literal {id}: that is how a message can
# explain the flag without the explanation being eaten by it.
# EACH EXTRA PAIR OF BRACES PEELS ONE MORE LEVEL, and that is not a special case for two --
# it falls out of the leftmost-first sentinel swap below. Measured 2026-09-11 00:5x, after
# Anorak hit the wall the shorter rule leaves: a sentence that has to MENTION the doubled
# form cannot be written with the doubled form, because that renders as the single one.
#   {id}       -> 4518        (pointer)
#   {{id}}     -> {id}        (talking about the pointer)
#   {{{id}}}   -> {{id}}      (talking about the escape itself)
#   {{{{id}}}} -> {{{id}}}    (and so on)
# WHY IT IS WRITTEN DOWN: the rule that teaches this flag must name BOTH forms, so it needs
# the third level to exist. An undocumented capability is the same as a missing one for
# whoever is writing that sentence -- Marveen's own announcement broke on exactly this. Without this flag the
# two steps are separate commands, and whoever writes the result text has to PREDICT its own
# message id -- a number that does not exist yet. Measured 2026-09-10/11: four such rows in one
# night, two agents, all four off by one, and in every case the id in between belonged to the
# OTHER agent's receipt (Marveen 4477->4475 for 4476 and 4503->4501 for 4502; Anorak 4480->4482
# and 4489->4491). Not typos: a "+1" guess in a fleet where somebody else is also sending.
# So this flag REFUSES a result text that names a message id without {id} -- the point is not
# to make the right thing possible (it already was, in two commands) but to make the wrong
# thing impossible in one.
set -uo pipefail

# base dir = the parent of this script's dir (scripts/..), so it works from any CWD / any install
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${MARVEEN_WEB_PORT:-3420}"
TOKEN_FILE="$BASE/store/.dashboard-token"
URL="http://localhost:${PORT}/api/messages"
LOG="$BASE/store/agent-msg-failures.log"

CLOSE=""; RESULT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --close)  CLOSE="${2:?--close needs a message id}"; shift 2 ;;
    --result) RESULT="${2:?--result needs a text}"; shift 2 ;;
    --) shift; break ;;
    -*) echo "FAIL: unknown option $1"; exit 2 ;;
    *) break ;;
  esac
done
if [ -n "$CLOSE" ] && [ -z "$RESULT" ]; then echo "FAIL: --close needs --result too"; exit 2; fi
if [ -n "$RESULT" ] && [ -z "$CLOSE" ]; then echo "FAIL: --result needs --close too"; exit 2; fi
# THE GUARD. A result text that names a message id but has no {id} placeholder is a guess by
# construction, because the id does not exist until the send below returns. Refusing here is
# the whole point of the flag; a warning would leave the same decision to be made every time.
if [ -n "$RESULT" ]; then
  RESULT="$RESULT" python3 -c '
import os, re, sys
t = os.environ["RESULT"]
# A {{id}} EMLITES, nem pointer: aki csak BESZEL a helyorzorol, annak nincs mutatoja.
# Enelkul egy "msg 4510 ... {{id}}" szoveg atmenne a kapun, mert a {{id}} tartalmazza
# a {id} reszsztringet -- a kapu sajat vak pontja lett volna.
if "{id}" in t.replace("{{id}}", ""):
    sys.exit(0)
m = re.search(r"(?i)\bmsg(?:_id)?[: ]\s*#?(\d{3,})", t)
if m:
    sys.stderr.write(
        "FAIL: a --result szoveg egy uzenet-azonositot nevez meg (" + m.group(0).strip() + "), de nincs benne {id}.\n"
        "      KET ESET VAN, es ez az or nem tudja megkulonboztetni oket:\n"
        "      (a) A SAJAT, MOST KULDENDO uzenetedre hivatkozol. Az a szam meg nem letezik\n"
        "          (a kuldes UTAN keletkezik), tehat tippelned kellene. EZ a valodi hiba:\n"
        "          negy ilyen sor kelt egy ejszaka, ket agenstol, mind a negy elcsuszott.\n"
        "          MEGOLDAS: ird oda {id}-t a szam helyere, a helper behelyettesiti.\n"
        "      (b) Egy MAR ELKULDOTT uzenetre hivatkozol, ami letezik. Ez JOGOS, es ilyenkor\n"
        "          az or HAMISAN riaszt -- a szoveg alakja azonos, csak a szam multbeli.\n"
        "          MEGOLDAS: hagyd ki a szamot a --result szovegbol (a reszletek ugyis a\n"
        "          kuldott uzenetben allnak). NEM te tevedtel.\n"
        "      Az or szandekosan nem ellenorzi az API-n, hogy a szam letezik-e: egy lassu\n"
        "      vagy hibazo halozati hivas miatt egy AMUGY JO kuldes bukna el. (Anorak merte\n"
        "      2026-09-11 ejjel, es maga javasolta, hogy a logika ne valtozzon, csak ez a szoveg.)\n")
    sys.exit(2)
' || exit 2
fi

FROM="${1:?from required}"; TO="${2:?to required}"; C="${3:?content required (or - for STDIN)}"
[ "$C" = "-" ] && C="$(cat)"
[ -r "$TOKEN_FILE" ] || { echo "FAIL: no token file at $TOKEN_FILE"; exit 1; }
TOKEN="$(cat "$TOKEN_FILE")"

BODY="$(FROM="$FROM" TO="$TO" C="$C" python3 -c 'import json,os; print(json.dumps({"from":os.environ["FROM"],"to":os.environ["TO"],"content":os.environ["C"]}))')"

attempt=0; max=3; CODE=""; ID=""
while [ "$attempt" -lt "$max" ]; do
  attempt=$((attempt+1))
  RESP="$(curl -s -X POST "$URL" -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -d "$BODY" -w $'\n%{http_code}' 2>/dev/null || true)"
  CODE="$(printf '%s' "$RESP" | tail -n1)"
  JSON="$(printf '%s' "$RESP" | sed '$d')"
  # An id ALONE is not delivery. The router answers 200 WITH an id even when the
  # recipient is not running, and says so in a separate `warning` field
  # (src/web/routes/messages.ts) whose text spells out that such a message is
  # LOST rather than queued. This helper read only the id, so it printed
  # "OK id=..." for a message that never arrived -- the exact failure the header
  # above says it exists to prevent, one field further in. The exit code stays 0
  # on purpose: the row really was accepted, so this is not a send failure. It
  # just must not be silent.
  read -r ID WARN <<EOF
$(printf '%s' "$JSON" | python3 -c 'import sys,json
try:
  d=json.load(sys.stdin)
  if not isinstance(d,dict): d={}
except Exception:
  d={}
w=" ".join(str(d.get("warning","")).split())
print((d.get("id","") or "-"), w)' 2>/dev/null)
EOF
  [ "$ID" = "-" ] && ID=""
  if { [ "$CODE" = "200" ] || [ "$CODE" = "201" ]; } && [ -n "$ID" ]; then
    if [ -n "${WARN:-}" ]; then
      echo "OK id=$ID  WARNING: $WARN" >&2
      echo "OK id=$ID (warning)"
    else
      echo "OK id=$ID"
    fi
    if [ -n "$CLOSE" ]; then
      # The result text is assembled HERE, from the id the server just gave us. This is the
      # only point in the run where the number is a measurement rather than a prediction.
      # HASZNALAT kontra EMLITES. A csere eddig GLOBALIS volt, ezert egy result-szoveg,
      # ami MAGAROL A HELYORZOROL beszelt, szethullott: a 4505 lezarasaban a "{id} helyere
      # a visszakapott id kerul" mondatbol "a 4507 helyere..." lett, es a kapu leirasa is
      # ertelmet vesztette (Anorak merte, 2026-09-11 00:4x). Az elso eles hasznalat epp a
      # sajat bejelentese volt, es epp az serult meg.
      # Ezert: {{id}} EMLITES (literalis {id} lesz belole), {id} POINTER (cserelodik).
      # Egy eszkoz, ami nem tud kulonbseget tenni a ketto kozott, nem tudja atkuldeni a
      # sajat dokumentaciojat a sajat csatornajan.
      #
      # `notify: false`, ES EZ NEM OPCIO EZEN AZ AGON. Ez a flag SZERKEZETILEG mindig a
      # "mar kulon valaszoltam" eset: ugyanez a hivas KULDI a tartalmat es ZARJA a sort,
      # tehat a lezarasbol keletkezo nyugta olyan temarol ebreszti fel a cimzettet,
      # amirol masodperccel korabban bovebben is olvasott. A kozos CLAUDE.md szabalya
      # ("HA MAR KULON VALASZOLTAL RA, A LEZARAS LEGYEN notify: false") itt nem
      # megitelés kerdese, hanem a flag definiciojabol kovetkezik.
      # MERVE 2026-09-17, Roxy jelezte a sajat postajabol: aznap a bejovoje 46%-a
      # (6/13) ilyen duplikalt nyugta volt, es MIND A HAT kulon erdemi uzenetre
      # hivatkozott ("Reszletek: msg N"). Marveen visszamerte a sajat kimenojere:
      # 15 nyugta EGY nap alatt, mind ezen az agon.
      # Ha valaha kell a nyugta ezen az uton, az UJ flag legyen es lathato a
      # parancssorban -- egy elhagyott kapcsolo nem dontes, csak feledés.
      #
      # ES AMIT NE "EGYSEGESITS" (Roxy kerte leirni, 2026-09-17): a NYERS
      # `curl -X PUT /api/messages/<id>` agon a nyugta ERTESITO marad, es ez helyes --
      # ott a lezaras tenyleg lehet az egyetlen jelzes. Harom ut van, harom kulonbozo
      # helyes alapertelmezessel (kozvetlen DB-iras: nincs nyugta; nyers PUT: van;
      # ez a helper: nincs). A tablazat a docs/marveen-agent-reference.md-ben all.
      # Aki a harmat egy alapertelmezes ala rendezi, epp azt a kulonbseget tunteti el,
      # amiert igy allnak.
      #
      # ES AMIERT EZ NEM SZOKASON ALL: a --close mellett is KOTELEZO mind a harom
      # pozicionalis argumentum (lasd lentebb, `${1:?from required}`), tehat close-only
      # hivas ezen az uton NEM LETEZIK -- aki zar, az ugyanabban a hivasban kuldott is.
      CB="$(RESULT="$RESULT" ID="$ID" python3 -c '
import json, os
SENT = "\x00ID\x00"
t = os.environ["RESULT"]
t = t.replace("{{id}}", SENT)          # az emlitest kivonjuk a csere alol
t = t.replace("{id}", os.environ["ID"])  # a pointer megkapja a MERT szamot
t = t.replace(SENT, "{id}")            # az emlites literalisan ter vissza
print(json.dumps({"status": "done", "result": t, "notify": False}))')"
      CR="$(curl -s -X PUT "http://localhost:${PORT}/api/messages/${CLOSE}" -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -d "$CB" -w $'\n%{http_code}' 2>/dev/null || true)"
      CCODE="$(printf '%s' "$CR" | tail -n1)"
      if [ "$CCODE" = "200" ]; then echo "CLOSED $CLOSE"; else echo "FAIL close=$CLOSE http=${CCODE:-?}"; exit 1; fi
    fi
    exit 0
  fi
  sleep 1
done
echo "FAIL from=$FROM to=$TO http=${CODE:-?} id='$ID' (after $max tries)"
printf '%s\tFAIL\tfrom=%s\tto=%s\thttp=%s\tresp=%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$FROM" "$TO" "${CODE:-?}" "$(printf '%s' "${JSON:-}" | head -c 200)" >> "$LOG" 2>/dev/null || true
exit 1
