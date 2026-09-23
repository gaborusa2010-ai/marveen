#!/usr/bin/env bash
# anorak-start-watch.sh -- report what actually happened at Anorak's 23:00 start.
#
# Why this exists as a script and not as a promise
# ------------------------------------------------
# 2026-09-01: Forgacs told Marveen "23:00 utan visszanezem a journalt". Then he
# checked, and found that NOTHING would wake him at 23:00 -- no scheduled task
# addressed to him, no timer. An agent only runs when something runs it, so the
# promise had no mechanism behind it and the report would simply never have
# arrived. That is the same failure the whole day was about: an operation that
# looks done because someone said it, with nothing measured behind it.
#
# So the watch is a script. It needs no agent awake, and it reports whether the
# start SUCCEEDED, not merely whether the unit ran.
#
# What it reads
# -------------
# marveen-anorak-shift-start.service is a oneshot fired by a timer at 23:00. Its
# journal carries the three things we could not measure in advance:
#   - "varakozas=NNs"  how long the agent actually took to come up. The script
#                      used to wait a fixed 20s and give up; it now polls to 180s
#                      and prints the real figure. This is the number nobody could
#                      estimate, only wait for.
#   - "runState=... tmux=..."  the two independent confirmations.
#   - "eligazitas kikuldve"    whether a briefing FILE existed at start time. A
#                      start that cannot be confirmed exits before briefing, so
#                      Anorak would work all night not knowing about the new gates.
#                      NOTE (2026-09-08): this is not the same as "the agent got
#                      no briefing". The main agent usually sends one by message
#                      right after this report -- because of it. The field is
#                      worded accordingly; do not read a "nem" as an idle agent.
#
# Usage:  anorak-start-watch.sh [--since "23:00"] [--no-send]
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=marveen-anorak-shift-start.service
# journalctl does NOT accept "today 22:55" -- it fails with "Failed to parse
# timestamp". Measured 2026-09-01, first run of this script. An absolute
# "YYYY-MM-DD HH:MM:SS" is accepted, so it is built from date(1).
# 2026-09-09: this was plain "$(date +%F) 22:55:00" -- TODAY's date. That is right
# at 23:05 and wrong at every other hour. The service ran at 05:05 on a boot (the
# timer's Requires= pulled it in; fixed the same day), "today 22:55" was then 18
# hours in the FUTURE, so the window could not contain a hit, and the script
# reported "EL SEM INDULT" about a shift that had actually run 23:00:18 -> 04:14:07.
# The anchor is therefore the most recent 22:55 that is already in the past.
SINCE="$(date +%F) 22:55:00"
if [ "$((10#$(date +%H%M)))" -lt 2255 ]; then
  SINCE="$(date -d yesterday +%F) 22:55:00"
fi
SEND=yes

while [ $# -gt 0 ]; do
  case "$1" in
    --since) SINCE="$2"; shift 2 ;;
    --no-send) SEND=no; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "ismeretlen kapcsolo: $1" >&2; exit 2 ;;
  esac
done

# A window that starts in the future cannot contain a hit, so an empty result from
# it says nothing about Anorak. Same principle as the journalctl-failure branch
# below: refuse to judge rather than judge from a broken measurement.
SINCE_EPOCH="$(date -d "$SINCE" +%s 2>/dev/null || echo 0)"
if [ "$SINCE_EPOCH" -gt "$(date +%s)" ]; then
  echo "ANORAK EJSZAKAI INDULAS -- NEM TUDOM MEGMONDANI" >&2
  echo "A vizsgalt ablak kezdete ($SINCE) a JOVOBEN van, tehat az ures eredmeny garantalt. NEM allitok semmit Anorakrol." >&2
  exit 2
fi

LOG="$(journalctl --user -u "$UNIT" --since "$SINCE" --no-pager 2>&1)"
JRC=$?

# A reader that cannot read must SAY SO, not fall through to a verdict. The
# first run of this script did exactly that: journalctl rejected the timestamp,
# and the script still printed "BIZONYTALAN" -- a judgement about Anorak based
# on an error message about a date. A failed measurement is not a measurement.
if [ $JRC -ne 0 ]; then
  echo "ANORAK EJSZAKAI INDULAS -- NEM TUDOM MEGMONDANI" >&2
  echo "A journalctl maga hibazott (exit $JRC), tehat NEM Anorakrol allitok semmit:" >&2
  printf '%s\n' "$LOG" >&2
  exit 2
fi

if [ -z "$LOG" ] || printf '%s' "$LOG" | grep -q '^-- No entries'; then
  VERDICT="EL SEM INDULT"
  DETAIL="A $UNIT egyetlen bejegyzest sem irt a journalba \"$SINCE\" ota. Vagy a timer nem sult el, vagy a gep aludt. Anorak valoszinuleg NEM fut."
else
  WAIT="$(printf '%s' "$LOG"  | grep -o 'varakozas=[0-9]*s'        | tail -1)"
  STATE="$(printf '%s' "$LOG" | grep -o 'runState=[a-z]* tmux=[a-z]*' | tail -1)"
  BRIEF="$(printf '%s' "$LOG" | grep -c 'eligazitas kikuldve')"
  FAILED="$(printf '%s' "$LOG" | grep -c 'FIGYELEM: az inditas nem igazolt')"

  # 2026-09-22, ket este egymas utan (09-21 es 09-22, azonos szoveggel): a unit
  # "Agent is already running"-ot kapott, mert az agens mar futott, es ez az ag
  # BIZONYTALAN-t irt ra -- holott ugyanabban a logban ott allt a KET fuggetlen
  # igazolas (runState=running tmux=igen). A jelentes tehat pontos volt es
  # megis felrevezeto: a fo-agensnek mindket este kezzel kellett visszamernie,
  # hogy nincs baj. Egy naponta ismetlodo, helyes riasztas ket agens forduloját
  # viszi el. A "mar futott" sajat verdikt, nem bizonytalansag.
  ALREADY=no
  if [ "$FAILED" -gt 0 ]; then
    VERDICT="INDULAS NEM IGAZOLT"
  elif printf '%s' "$LOG" | grep -q 'anorak start ok'; then
    VERDICT="ELINDULT"
  elif printf '%s' "$LOG" | grep -qE 'mar ebben az allapotban|Agent is already running' \
       && [ "$STATE" = "runState=running tmux=igen" ]; then
    VERDICT="MAR FUTOTT"
    ALREADY=yes
  else
    VERDICT="BIZONYTALAN"
  fi

  # 2026-09-08, Marveen merte meg: ez a mezo SZERKEZETILEG mindig "nem" lesz, ha
  # a fo-agens uzenetben reagal. Aznap a jelzes 23:00:26-kor ment ki "NEM"-mel, es
  # az eligazitas 23:01:58-kor megerkezett -- 92 masodperccel kesobb, EPP mert a
  # jelzes szolt. A mezo tehat nem azt meri, hogy az agens eligazitas nelkul
  # maradt-e, hanem hogy volt-e FAJL az inditas pillanataban. A regi cimke a ket
  # allitast osszemosta, es egy kesobbi olvaso abbol azt olvasta volna ki, hogy
  # Anorak ures prompton dolgozott egesz ejjel.
  if [ "$BRIEF" -gt 0 ]; then
    BRIEF_TXT="eligazitas-fajl: volt, kikuldve"
  else
    BRIEF_TXT="eligazitas-fajl: NEM volt az inditaskor (ettol meg kaphat eligazitast uzenetben, es tipikusan epp ezutan kap)"
  fi
  DETAIL="varakozas: ${WAIT:-nincs adat} | igazolas: ${STATE:-nincs adat} | $BRIEF_TXT"
  if [ "$ALREADY" = yes ]; then
    DETAIL="$DETAIL
A unit egy MAR ELO agenst talalt, es helyesen nem inditott masodikat. Ez a unit
szempontjabol nem hiba. Amit viszont NEM igazol: hogy friss kontextussal kezdi
az ejszakat -- azt a lenti contextTokens mondja meg."
  fi
fi

# A start is not proven by the unit's own log alone. Ask the API too, the way
# the start script itself does: two independent sources, never one.
TOKEN="$(cat "$BASE/store/.dashboard-token" 2>/dev/null)"
AGENTS_JSON="$(curl -s -m 20 -H "Authorization: Bearer $TOKEN" "http://localhost:3420/api/agents" 2>/dev/null)"
NOW_STATE="$(printf '%s' "$AGENTS_JSON" | AGENT=anorak python3 "$BASE/scripts/agent-runstate.py" 2>/dev/null)"
# A kontextus merete az egyetlen szam, ami eldonti, hogy erdemes-e friss indulast
# kerni -- es eddig kezzel kellett lekerni. Ures marad, ha az API nem valaszol.
CTX_INFO="$(printf '%s' "$AGENTS_JSON" | AGENT=anorak python3 "$BASE/scripts/agent-context-info.py" 2>/dev/null)"
TMUX_NOW=nem
tmux ls 2>/dev/null | grep -q 'agent-anorak' && TMUX_NOW=igen

MSG="ANORAK EJSZAKAI INDULAS -- $VERDICT
$DETAIL
Most (a jelentes pillanataban): runState=${NOW_STATE:-ismeretlen} tmux=$TMUX_NOW
Kontextus: ${CTX_INFO:-nincs adat (az /api/agents nem valaszolt)}

A journal nyers sorai:
$(printf '%s' "$LOG" | tail -12)"

echo "$MSG"

if [ "$SEND" = yes ]; then
  OUT="$(bash "$BASE/scripts/agent-msg.sh" forgacs marveen - <<< "$MSG" 2>&1)"
  case "$OUT" in
    *"OK id="*) echo "[jelentes elkuldve: $OUT]" ;;
    *)          echo "[JELENTES NEM MENT EL: $OUT]" >&2; exit 1 ;;
  esac
fi
