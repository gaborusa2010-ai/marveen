#!/usr/bin/env bash
# agent-night-shift.sh -- start or stop ONE agent for a night shift.
#
# Why a timer pair and not autoRestart
# ------------------------------------
# `autoRestart.dailyTime` (03:00 for cody/forgacs/roxy) RESTARTS an agent that is
# already running, to refresh its context. It does not START a stopped one. There
# is no "begin work at 23:00" setting in the system, so a genuine night shift has
# to be built: start at the beginning, stop at the end.
#
# The alternative -- leave the agent running all day like the others -- was
# rejected on the house rule in the agens-inditas-atadas skill: "a running agent
# consumes the owner's quota even with nothing to do". A researcher whose whole
# job happens between 23:00 and 06:30 would idle for sixteen hours a day.
#
# Checks the ANSWER, never curl's exit code: curl returns 0 on a 401 too.
# "Already in the wanted state" counts as success, because the goal is a state,
# not an action -- the same rule as agent-budget-guard.py.
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# WHY THE BRIEFING LIVES IN A FUNCTION (card 6e07d9c2, 2026-09-04)
#
# The test for this used to define its own run_branch() and check THAT. Forgacs
# found it on 2026-09-03: after he changed the script the suite reported 8/8
# green, and one of the eight was named "does nothing without a briefing" -- an
# assertion his change had just invalidated. The green did not mean the code was
# right; it meant the SECOND COPY was unchanged.
#
# That is the javitas-minden-peldanya pattern with a new carrier. In every other
# carrier (code, memory, card, doc) a stale copy is SILENT. In a test it is
# louder and worse: it actively asserts that something was checked.
#
# So there is one copy, here, and the test sources this file and calls this
# function. The check that the wiring is real: change the FUNCTION and the test
# must go red. If it stays green, there are two copies again.
#
# Sourcing is safe because the top-level flow below runs only when this file is
# executed directly (the BASH_SOURCE guard at the bottom). A sourced copy
# defines the function and does nothing else -- no API call, no token read.
send_night_briefing() {
  local BASE="$1" AGENT="$2"
  local BRIEF OUT
  # NIGHTBRIEF901: a start with no handover is exactly what the
  # agens-inditas-atadas skill warns about -- "az inditas maga egy API-hivas; a
  # MUNKA az atadas". And there is a second, sharper reason to do it HERE:
  #
  # 2026-09-01, measured. Two new PreToolUse gates went live during the day and
  # every agent was told about them -- except Anorak, who is stopped between
  # 06:30 and 23:00. The message could not be delivered ("csak futo agensnek
  # lehet uzenni") and the fleet only learned this from a [handoff-failure]
  # notice hours later. A night worker would otherwise have met two new gates
  # at 2am with nobody awake to explain them.
  #
  # So: a briefing left in store/night-briefing-<agent>.txt waits for the agent
  # to EXIST, and is delivered the moment its start is confirmed. Sent through
  # agent-msg.sh, which reports FAIL on a rejected send -- the file is only
  # retired after a confirmed id, so a failed delivery is retried next start
  # instead of vanishing.
  # 2026-09-20: a szkript mar nem csak ejszakai muszakra megy (Cody es Roxy
  # reggeli inditoja is ezt hivja, a gazda kerese, Telegram msg 3124). Az uj,
  # semleges nev az elsodleges; a regi megmarad, hogy Anorak folyamatban levo
  # esti rendje egy sorral se valtozzon.
  BRIEF="$BASE/store/briefing-$AGENT.txt"
  [ -s "$BRIEF" ] || BRIEF="$BASE/store/night-briefing-$AGENT.txt"
  if [ -s "$BRIEF" ]; then
    if OUT="$(bash "$BASE/scripts/agent-msg.sh" marveen "$AGENT" - < "$BRIEF" 2>&1)"; then
      case "$OUT" in
        *"OK id="*)
          mv "$BRIEF" "$BRIEF.sent-$(date +%Y%m%d-%H%M%S)"
          echo "$(date '+%F %T') $AGENT eligazitas kikuldve ($OUT)"
          ;;
        *)
          echo "$(date '+%F %T') $AGENT eligazitas NEM igazolt, a fajl marad: $OUT" >&2
          ;;
      esac
    else
      echo "$(date '+%F %T') $AGENT eligazitas kuldes HIBA, a fajl marad: $OUT" >&2
    fi
  else
    # NIGHTBRIEF901-B (Forgacs, 2026-09-03, Marveen merese ugyanaznap este).
    # A blokk eddig csak a fajl LETEZESERE reagalt, tehat pontosan akkor
    # hallgatott, amikor elfelejtettuk megirni az eligazitast. A naploban a
    # "nem volt mit mondani" es a "senki nem irta meg" ugyanugy nezett ki:
    # sehogy. 2026-09-03-an Anorak igy allt ures prompton 23:05-kor, es ez csak
    # kezzel, utolag derult ki.
    #
    # A jelzes NEM zajos: megmerve 09-01 es 09-02 estejen VOLT fajl, tehat a
    # hianya esemeny, nem alapallapot. Es kifejezetten a fo-agensnek szol, mert
    # o az, aki meg tudja irni -- az ejszakai munkasnak ebbol nincs haszna.
    echo "$(date '+%F %T') $AGENT NINCS eligazitas-fajl ($BRIEF), ures prompton indul"
    if OUT="$(bash "$BASE/scripts/agent-msg.sh" marveen marveen - <<EOF
[INDULAS ELIGAZITAS NELKUL] $AGENT elindult, es NINCS eligazitas-fajl: $BRIEF
Ures prompton all. Ha van mit atadni neki, kuldd el kozvetlen uzenetben most.
Ezt a sort az agent-night-shift.sh irja, nem maga az agens.
EOF
    )"; then
      # A haz szabalya: egy uzenet CSAK akkor ment el, ha jott vissza id. Az
      # agent-msg.sh FAIL-t ir 0-s kileposi koddal is, tehat az exit statusz
      # onmagaban nem eleg -- ugyanaz a csapda, amit a briefing-ag mar kezel.
      case "$OUT" in
        *"OK id="*)
          echo "$(date '+%F %T') $AGENT eligazitas-hiany jelezve ($OUT)"
          ;;
        *)
          echo "$(date '+%F %T') $AGENT eligazitas-hiany jelzes NEM igazolt: $OUT" >&2
          ;;
      esac
    else
      echo "$(date '+%F %T') $AGENT eligazitas-hiany jelzes HIBA: $OUT" >&2
    fi
  fi
}

main() {
  PORT="${MARVEEN_WEB_PORT:-3420}"
  AGENT="${1:?agent name required}"
  ACTION="${2:?start|stop required}"
  TOKEN="$(cat "$BASE/store/.dashboard-token")"

  RESP="$(curl -s -m 30 -w $'\n%{http_code}' -X POST \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' \
    "http://localhost:$PORT/api/agents/$AGENT/$ACTION" 2>/dev/null || true)"
  CODE="$(printf '%s' "$RESP" | tail -n1)"
  BODY="$(printf '%s' "$RESP" | sed '$d')"

  case "$CODE" in
    200|201) echo "$(date '+%F %T') $AGENT $ACTION ok" ;;
    400)
      # "Agent is not running" / "already running": the wanted state already holds.
      echo "$(date '+%F %T') $AGENT $ACTION: mar ebben az allapotban ($BODY)"
      ;;
    *)
      echo "$(date '+%F %T') $AGENT $ACTION HIBA http=$CODE $BODY" >&2
      exit 1
      ;;
  esac

  # A start is not proven by a 200. Confirm from a second, independent source.
  if [ "$ACTION" = "start" ]; then
    # WAIT901 (Forgacs, 2026-09-01, before the first real run): this used to be a
    # single `sleep 20` followed by a hard exit. A cold agent that needed 21
    # seconds therefore failed the confirmation, and -- because the BRIEFING is
    # sent below, after this check -- it would have worked all night without the
    # handover, with the file waiting until 23:00 the next day. The cost of
    # guessing too short was a whole silent night; the cost of waiting is seconds.
    # So: poll instead of guess, and PRINT how long it actually took, so the next
    # reader has a measurement rather than someone's estimate of a good timeout.
    WAITED=0
    MAXWAIT=180
    STATE=""
    TMUX_OK=nem
    while [ "$WAITED" -lt "$MAXWAIT" ]; do
      sleep 5
      WAITED=$((WAITED + 5))
      STATE="$(curl -s -m 20 -H "Authorization: Bearer $TOKEN" "http://localhost:$PORT/api/agents" \
        | AGENT="$AGENT" python3 "$BASE/scripts/agent-runstate.py")"
      TMUX_OK=nem
      tmux ls 2>/dev/null | grep -q "agent-$AGENT" && TMUX_OK=igen
      [ "$STATE" = "running" ] && [ "$TMUX_OK" = "igen" ] && break
    done
    echo "$(date '+%F %T') $AGENT runState=$STATE tmux=$TMUX_OK varakozas=${WAITED}s"
    if [ "$STATE" != "running" ] || [ "$TMUX_OK" != "igen" ]; then
      echo "FIGYELEM: az inditas ${MAXWAIT}s alatt sem igazolt ket fuggetlen forrasbol" >&2
      exit 1
    fi

    send_night_briefing "$BASE" "$AGENT"
  fi
}

# Executed directly -> do the work. Sourced (the test) -> only the function
# above is defined, so a `source` cannot start or stop an agent by accident.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
