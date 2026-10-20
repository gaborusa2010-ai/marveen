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
# Usage:  bash scripts/agent-msg.sh [--priority high|normal] <from> <to> "<content>"
#   content: plain text (quotes / newlines OK) -- the body is built with json.dumps (no quoting pitfalls).
#   large / multi-line content may come from STDIN when the 3rd arg is "-":
#     echo "<long text>" | bash scripts/agent-msg.sh <from> <to> -
# Output: success -> "OK id=<n> queue=<depth> (~<n> min)"; failure -> "FAIL <reason>"
#         + a line in store/agent-msg-failures.log, exit 1.
#   The queue fields come from the POST response: a message is accepted instantly
#   but only DELIVERED into an idle gap in the recipient's pane, which on a busy
#   agent was measured at 80+ minutes. "queue=" is the recipient's pending count
#   including this message; "(~n min)" is the median delivery delay of its recent
#   messages and is OMITTED when there is no history (unknown is not "instant").
#   Both are absent against a server that does not return them, so the line is
#   then exactly "OK id=<n>" as before.
#   A stopped target: "OK id=<n> ... (queued: target not running,
#   delivered when it starts)" -- the row is PENDING, do NOT re-send.
#   At MARVEEN_QUEUE_WARN_AT (default 3) or more waiting, a stderr notice says so
#   at the moment the sender decides whether to send the next one.
#   --priority (card ad771121): "high" puts the row ahead of the recipient's normal rows; absent means
#     "normal" (FIFO, the server default). Any other value fails here, before anything is sent. A sender
#     over its hourly high budget toward that recipient gets the row accepted as normal (never dropped),
#     and a stderr NOTICE says DOWNGRADED. The switch goes BEFORE <from>; an empty value, or the switch
#     anywhere else (after the sender, the recipient or the content), also fails here, before anything
#     is sent.
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
#
# LOG FORMAT, store/agent-msg-failures.log (tab-separated, one line per failure):
#   <YYYY-MM-DD HH:MM:SS>  FAIL  from=<a>  to=<b>  url=<endpoint>  http=<code>  resp=<first 200 bytes>
# CHANGED 2026-09: the `url=` field is NEW. It was added together with the
# env-overridable base URL, because from that point a failure can mean "posted to
# the wrong address" and the old line could not distinguish that from a dead
# server. A parser written against the pre-2026-09 format sees one extra field;
# parse by the `key=` names, not by position.
# Env:
#   MARVEEN_API_BASE   full base URL, e.g. https://marveen.example.com (overrides host+port)
#   MARVEEN_WEB_PORT   port for the default localhost base (default 3420)
#   MARVEEN_TOKEN_FILE bearer token file (default <repo>/store/.dashboard-token)
#   MARVEEN_HOMOGLYPH_BIN  the checker (default <repo>/scripts/lib/homoglyph.py)
#   MARVEEN_QUEUE_WARN_AT  queue depth that triggers the stderr notice (default 3, 0 = off)
# MEASURED 2026-09-13: a remote agent runs this helper OUTSIDE this repo, where localhost:3420
# does not exist -- it had to fall back to raw curl, i.e. exactly the unchecked pattern this file was
# written to eliminate. A hardcoded base URL silently un-installs the helper for everyone not on this
# VM, so the base is env-overridable and the two endpoints stay ONE script.
set -uo pipefail

# base dir = the parent of this script's dir (scripts/..), so it works from any CWD / any install
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Dashboard port, the doctor.sh chain: MARVEEN_WEB_PORT, then WEB_PORT from the
# environment, then WEB_PORT from this install's .env, then 3420. An agent's tmux
# session sets neither variable, so on an install with a non-default WEB_PORT the
# env-only lookup sent every message to 3420 (#1869).
PORT="${MARVEEN_WEB_PORT:-${WEB_PORT:-$(sed -n 's/^WEB_PORT=//p' "$BASE/.env" 2>/dev/null | head -1 | tr -d ' "'"'"''; true)}}"
PORT="${PORT:-3420}"
API_BASE="${MARVEEN_API_BASE:-http://localhost:${PORT}}"
API_BASE="${API_BASE%/}"
TOKEN_FILE="${MARVEEN_TOKEN_FILE:-$BASE/store/.dashboard-token}"
URL="${API_BASE}/api/messages"
LOG="$BASE/store/agent-msg-failures.log"

PRIORITY=""; PRIORITY_GIVEN=0
case "${1:-}" in
  --priority) PRIORITY_GIVEN=1; PRIORITY="${2:-}"; if [ "$#" -ge 2 ]; then shift 2; else shift; fi ;;
  --priority=*) PRIORITY_GIVEN=1; PRIORITY="${1#--priority=}"; shift ;;
esac
# Measured by the card's independent test (card ad771121): an empty value went out as normal, a switch
# after the content was silently dropped, and one after the sender took the recipient's place. A given
# switch must carry one of the two words, and it may stand only before <from>.
if [ "$PRIORITY_GIVEN" = 1 ] && [ "$PRIORITY" != "high" ] && [ "$PRIORITY" != "normal" ]; then
  echo "FAIL: --priority must be high or normal (absent = normal)"; exit 1
fi
for arg in "$@"; do
  case "$arg" in
    --priority|--priority=*) echo "FAIL: --priority goes before <from>: agent-msg.sh [--priority high|normal] <from> <to> <content>; nothing was sent"; exit 1 ;;
  esac
done
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

# --- Homoglyph gate, BEFORE the payload is built (MSGGATE924) --------------
# On the RAW text, not on the JSON: json.dumps escapes a Cyrillic letter into
# \uXXXX, and a checker reading the encoded body would be looking at a string
# where the problem is no longer visible as a letter. The gate has to see what
# the sender typed.
#
# WHY IT IS HERE AND NOT IN EACH AGENT'S TOOLBOX. Measured 2026-09-24 across
# three agents: two had built this guard for themselves, independently, because
# both had been bitten by it; the third had no guard at all. And no CLAUDE.md
# prescribes those private wrappers -- they all name THIS helper. The result was
# predictable in hindsight: the agent who WROTE such a wrapper spent a whole day
# calling this script directly, with the check run beside it in a separate
# command instead of in front of it. One message went out contaminated while the
# checker printed "NEM KULDOM EL" next to it. The rule that needs remembering is
# not a rule; it has to sit in the path of the action.
#
# FAIL-OPEN ON A MISSING CHECKER, AND LOUDLY -- a deliberate exception to the
# repo's usual fail-closed stance for gates (see .git/hooks/pre-commit.d). This
# helper is the fleet's mandated message route: blocking every inter-agent
# message on an install whose lib file is absent would be a far worse failure
# than the one being prevented, and it would be a NEW failure, not today's. A
# missing checker is exactly today's state, so the honest behaviour is to send
# and say so. Contaminated text with the checker PRESENT is refused, which is
# where the closed direction belongs.
# Overridable so the suite can measure the missing-checker branch too.
HG="${MARVEEN_HOMOGLYPH_BIN:-$BASE/scripts/lib/homoglyph.py}"
if [ -r "$HG" ] && command -v python3 >/dev/null 2>&1; then
  # THE CHECKER IS A VERDICT, NOT A FILTER: what goes out is the text the
  # sender typed, never the checker's stdout. Measured in the 2026-09-24 review
  # of #1541: a checker that exits 0 with EMPTY stdout made this helper send an
  # empty message and report OK -- a broken tool silently replaced the message
  # instead of failing. The exit code is the only thing read here.
  printf '%s' "$C" | python3 "$HG" >/dev/null
  HG_RC=$?
  case "$HG_RC" in
    0) : ;;
    # 3 is the checker's one documented refusal code; anything else is the
    # CHECKER failing, not the text. They must not share a message: "refused"
    # sends the sender to rewrite a word that may be perfectly fine, while a
    # crashed checker is an unmeasured send and the operator's problem.
    3) echo "FAIL: homoglyph gate refused the message; nothing was sent." >&2
       exit 3 ;;
    *) echo "FAIL: homoglyph checker CRASHED (rc=$HG_RC) at $HG; nothing was sent." >&2
       echo "  This is not a verdict on the text -- fix or unset MARVEEN_HOMOGLYPH_BIN." >&2
       exit 4 ;;
  esac
else
  echo "WARN: homoglyph checker not found at $HG -- sending UNCHECKED." >&2
fi

BODY="$(FROM="$FROM" TO="$TO" C="$C" P="$PRIORITY" python3 -c 'import json,os
d={"from":os.environ["FROM"],"to":os.environ["TO"],"content":os.environ["C"]}
if os.environ.get("P"): d["priority"]=os.environ["P"]
print(json.dumps(d))')"

attempt=0; max=3; CODE=""; ID=""
while [ "$attempt" -lt "$max" ]; do
  attempt=$((attempt+1))
  RESP="$(curl -s -X POST "$URL" -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -d "$BODY" -w $'\n%{http_code}' 2>/dev/null || true)"
  CODE="$(printf '%s' "$RESP" | tail -n1)"
  JSON="$(printf '%s' "$RESP" | sed '$d')"
  # An id ALONE is not delivery. The router answers 200 WITH an id even when the
  # recipient is not running, and says so in a separate `warning` field
  # (src/web/routes/messages.ts). For a stopped LOCAL target (`queued: true`)
  # the row is PENDING and is delivered when the target starts, inside the
  # router's abandon window; only after that window does it fail.
  # This helper read only the id, so it printed "OK id=..." with nothing about
  # the recipient not running -- the failure the header above says it exists to
  # prevent, one field further in. The exit code stays 0 on purpose: the row
  # really was accepted, so this is not a send failure. It just must not be
  # silent, and a queued row must not be re-sent (a duplicate).
  #
  # The queue state rides in the same parse. "-" stands for an empty field so
  # `read` cannot shift the warning text into it. The delay is rounded UP to at
  # least one minute: a 20-second median printed as "~0 min" would read as
  # "instant", the same lie a NULL flattened to 0 would tell.
  read -r ID DEPTH MINS QUEUED WARN <<EOF
$(printf '%s' "$JSON" | python3 -c 'import sys,json,math
try:
  d=json.load(sys.stdin)
  if not isinstance(d,dict): d={}
except Exception:
  d={}
q=d.get("queue") if isinstance(d.get("queue"),dict) else {}
depth=q.get("queueDepth")
delay=q.get("estimatedDelaySec")
depth="-" if not isinstance(depth,int) or isinstance(depth,bool) else str(depth)
mins="-" if not isinstance(delay,(int,float)) or isinstance(delay,bool) else str(max(1,math.ceil(delay/60)))
w=" ".join(str(d.get("warning","")).split())
qd="Q" if d.get("queued") is True else "-"
print((d.get("id","") or "-"), depth, mins, qd, w)' 2>/dev/null)
EOF
  [ "$ID" = "-" ] && ID=""
  [ "${DEPTH:-}" = "-" ] && DEPTH=""
  [ "${MINS:-}" = "-" ] && MINS=""
  if { [ "$CODE" = "200" ] || [ "$CODE" = "201" ]; } && [ -n "$ID" ]; then
    # "OK id=<n>" stays the prefix: callers and docs grep for it.
    LINE="OK id=$ID"
    [ -n "${DEPTH:-}" ] && LINE="$LINE queue=$DEPTH"
    [ -n "${MINS:-}" ] && LINE="$LINE (~${MINS} min)"
    if [ "${QUEUED:-}" = "Q" ]; then
      # The target is stopped, the row is PENDING and is delivered
      # when the target starts (within the router's abandon window). Not a send
      # failure, and NOT a reason to re-send: a re-send only duplicates it.
      # The stdout keeps the "OK id=" prefix on purpose: callers (and the fleet
      # docs) treat exactly that prefix as "accepted", and a new verb would read
      # as a failure and trigger the very re-send this branch exists to stop.
      echo "QUEUED id=$ID  NOTE: $WARN" >&2
      echo "$LINE (queued: target not running, delivered when it starts)"
    elif [ -n "${WARN:-}" ]; then
      echo "$LINE  WARNING: $WARN" >&2
      echo "$LINE (warning)"
    else
      echo "$LINE"
    fi
    # Printing the number is not enough on its own: the sender has already
    # sent by the time they read it. Say it on stderr, at the moment the next
    # send is being decided. Not a refusal -- an urgent message must still get
    # through.
    WARN_AT="${MARVEEN_QUEUE_WARN_AT:-3}"
    case "$WARN_AT" in ''|*[!0-9]*) WARN_AT=3 ;; esac
    if [ -n "${DEPTH:-}" ] && [ "$WARN_AT" -gt 0 ] && [ "$DEPTH" -ge "$WARN_AT" ]; then
      echo "NOTICE: $DEPTH message(s) are waiting for $TO${MINS:+, measured delay ~${MINS} min}." >&2
      echo "  It is busy; prefer adding to an existing message or a shared card over another message." >&2
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
      CR="$(curl -s -X PUT "${URL}/${CLOSE}" -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -d "$CB" -w $'\n%{http_code}' 2>/dev/null || true)"
      CCODE="$(printf '%s' "$CR" | tail -n1)"
      if [ "$CCODE" = "200" ]; then echo "CLOSED $CLOSE"; else echo "FAIL close=$CLOSE http=${CCODE:-?}"; exit 1; fi
    fi
    # Card ad771121: a high request over the pair's hourly budget went in as normal (accepted, never
    # dropped); the server says so with "downgraded": true, and the sender hears it here.
    if [ "$PRIORITY" = "high" ] && printf '%s' "$JSON" | python3 -c 'import sys,json
try:
  d=json.load(sys.stdin)
except Exception:
  d={}
sys.exit(0 if isinstance(d,dict) and d.get("downgraded") is True else 1)' 2>/dev/null; then
      echo "NOTICE: DOWNGRADED -- the row to $TO was accepted as normal: the hourly high budget toward it is used up (MESSAGE_HIGH_PRIORITY_PER_HOUR)." >&2
    fi
    exit 0
  fi
  sleep 1
done
echo "FAIL from=$FROM to=$TO url=$URL http=${CODE:-?} id='$ID' (after $max tries)"
printf '%s\tFAIL\tfrom=%s\tto=%s\turl=%s\thttp=%s\tresp=%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$FROM" "$TO" "$URL" "${CODE:-?}" "$(printf '%s' "${JSON:-}" | head -c 200)" >> "$LOG" 2>/dev/null || true
exit 1
