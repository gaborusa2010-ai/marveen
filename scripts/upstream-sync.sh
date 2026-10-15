#!/usr/bin/env bash
# upstream-sync.sh -- keep the local main mergeable so update.sh can ever run.
#
# WHY THIS EXISTS
# ---------------
# This install develops on the same branch it updates from. `main` carries 56+
# commits of our own work (muhely, invoice, kanban, heartbeat, quarantine
# reader, egress, backup) that will never exist upstream. update.sh pulls with
# --ff-only. The moment upstream (Szotasz/marveen) cuts a release, the two
# histories have BOTH moved, ff-only cannot apply, and the update dies in the
# pull phase -- 2026-08-27, 08-30, 08-31 13:52 and 09-01 all failed this way.
#
# The failure was never a build or a network problem. It is structural, so it
# recurs at EVERY upstream release, and the fix applied so far -- a hand-run
# `git merge origin/main` -- resolved one instance and left the cause standing.
# The owner reasonably believed it had been fixed for good. It had not.
#
# WHY A SEPARATE SCRIPT AND NOT A PATCH TO update.sh
# --------------------------------------------------
# update.sh is upstream's file. Teaching it to merge on divergence would be a
# change to shared code, so it needs a PR and it would then also merge on every
# other install that has no local commits. This script is ours: it lives beside
# the other watchers, needs no upstream agreement, and can be removed without
# touching anything shared.
#
# WHY IT DOES NOT RUN update.sh AFTERWARDS
# ----------------------------------------
# A merge is local, cheap and reversible; a rebuild + restart of the live
# system is not, and it is the owner's call. This script only removes the
# blocker and says so. update.sh then works unassisted: its ff-only pull finds
# nothing left to fetch, but dist/.built-commit no longer matches HEAD, so its
# self-heal (update.sh:616) rebuilds and restarts exactly as on a real update.
#
# WHY IT REFUSES INSTEAD OF STASHING
# ----------------------------------
# If a file the merge touches is also modified in the working tree, this exits
# without merging and says which file. Unattended stash/pop at 04:00 can leave
# a half-applied tree that nobody is awake to see; a refusal costs one release
# cycle of delay and nothing else. Only the INTERSECTION blocks -- an unrelated
# dirty file (the normal state here) does not.
#
# Silent when there is nothing to do, which is almost always. It wakes the model
# only on a real merge or a real block. Exit 0 unless the environment is broken.
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE" || exit 2

BRANCH="main"
REMOTE="origin"
STATE="$BASE/store/upstream-sync.json"
LOCK="$BASE/store/.upstream-sync.lock"
TS="$(date '+%Y-%m-%d %H:%M:%S')"

# The PREVIOUS state, read BEFORE state() overwrites it. Needed because a
# blocked tick repeats every 15 minutes with the identical cause, and each
# notify() wakes the main agent for a decision that is NOT the agent's to make
# (committing or discarding the owner's local edits). Measured 2026-09-08: the
# timer fires every 15 min, so an unattended blocked state produces ~96
# identical wake-ups a day. The report is correct and it is also inert -- and
# an alert nobody can act on is the kind that gets ignored when it finally
# changes. So: the FIRST block notifies, the repeats stay quiet, and any CHANGE
# in the cause notifies again.
PREV_RESULT=""; PREV_DETAIL=""
if [ -r "$STATE" ]; then
  PREV_RESULT="$(/usr/bin/python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('result',''))" "$STATE" 2>/dev/null || echo '')"
  PREV_DETAIL="$(/usr/bin/python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('detail',''))" "$STATE" 2>/dev/null || echo '')"
fi

# Serialise: a fetch+merge must never overlap with itself or with a hand-run
# copy. Non-blocking -- if another run holds it, this tick is simply skipped.
exec 9>"$LOCK" || exit 2
flock -n 9 || exit 0

# state <result> <detail> -- always written, so the state is inspectable even
# on the quiet ticks. Python, not jq: jq is not installed on this host.
state() {
  /usr/bin/python3 - "$STATE" "$TS" "$1" "$2" <<'PY'
import json, sys
path, ts, result, detail = sys.argv[1:5]
try:
    json.dump({"last_run": ts, "result": result, "detail": detail},
              open(path, "w"), indent=2, ensure_ascii=False)
except OSError:
    pass
PY
}

notify() {
  RES="$(printf '%s' "$1" | bash "$BASE/scripts/agent-msg.sh" marveen marveen -)"
  case "$RES" in
    OK\ id=*) echo "$TS notified ($RES)" ;;
    # A failed notify must be loud in the journal: the merge already happened
    # and nobody would otherwise learn of it.
    *) echo "$TS NOTIFY-FAIL ($RES)" >&2 ;;
  esac
}

# --- guards ------------------------------------------------------------------
git rev-parse --git-dir >/dev/null 2>&1 || { state error "not a git repo"; exit 2; }

CUR="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
if [ "$CUR" != "$BRANCH" ]; then
  # Someone is working on a feature branch. Not our business, and merging into
  # it would be actively wrong.
  state skipped "branch=$CUR (nem $BRANCH)"; exit 0
fi

GITDIR="$(git rev-parse --git-dir)"
if [ -e "$GITDIR/MERGE_HEAD" ] || [ -d "$GITDIR/rebase-merge" ] || [ -d "$GITDIR/rebase-apply" ]; then
  state skipped "merge/rebase folyamatban"; exit 0
fi

# --- fetch -------------------------------------------------------------------
FETCH_OK=0
for i in 1 2 3; do
  if git fetch --quiet "$REMOTE" "$BRANCH" 2>/dev/null; then FETCH_OK=1; break; fi
  [ "$i" -lt 3 ] && sleep 5
done
[ "$FETCH_OK" = 1 ] || { state error "fetch sikertelen ($REMOTE/$BRANCH)"; echo "$TS fetch failed" >&2; exit 0; }

# left = commits only upstream has (behind), right = only we have (ahead).
COUNTS="$(git rev-list --left-right --count "$REMOTE/$BRANCH...HEAD" 2>/dev/null || echo '0 0')"
BEHIND="$(echo "$COUNTS" | awk '{print $1+0}')"
AHEAD="$(echo "$COUNTS" | awk '{print $2+0}')"

# Nothing new upstream: the common case, and the reason this is cheap.
[ "$BEHIND" -eq 0 ] && { state clean "ahead=$AHEAD behind=0"; exit 0; }

# Behind but not ahead: a plain fast-forward, which update.sh does correctly on
# its own. Not our job -- doing it here would only hide what the updater did.
[ "$AHEAD" -eq 0 ] && { state ff-possible "ahead=0 behind=$BEHIND"; exit 0; }

# --- diverged: the case this script exists for -------------------------------
MB="$(git merge-base HEAD "$REMOTE/$BRANCH")"
CLASH="$(comm -12 \
  <(git diff --name-only "$MB" "$REMOTE/$BRANCH" | sort) \
  <(git diff --name-only HEAD | sort))"

if [ -n "$CLASH" ]; then
  DETAIL="$(echo "$CLASH" | tr '\n' ' ')"
  NEWSTATE="nem mentett helyi modositas utkozne: $DETAIL"
  # Quiet repeat: same result AND same cause as last tick -> state only, no wake-up.
  if [ "$PREV_RESULT" = "blocked" ] && [ "$PREV_DETAIL" = "$NEWSTATE" ]; then
    state blocked "$NEWSTATE"
    echo "$TS BLOCKED (unchanged, not notified): $DETAIL" >&2
    exit 0
  fi
  state blocked "$NEWSTATE"
  notify "$(printf '%s\n' \
    "[UPSTREAM-SYNC] Az upstream kiadott ${BEHIND} commitot, de NEM tudtam osszefesulni." \
    "Ok: ezeket a fajlokat a frissites is modositja, es a munkakonyvtarban is modositva vannak:" \
    "$DETAIL" \
    "Nem nyultam hozzajuk. Amig ez all, a frissites tovabbra is elbukik a pull fazisban." \
    "Teendo: commitold vagy dobd el ezeket a helyi modositasokat, aztan: bash scripts/upstream-sync.sh")"
  echo "$TS BLOCKED by local edits: $DETAIL" >&2
  exit 0
fi

OLD="$(git rev-parse HEAD)"
# MARVEEN_PROD_COMMIT_OK=1 is the pre-commit guard's own documented override
# (.git/hooks/pre-commit.d/05-prod-tree-guard). The guard exists to stop feature
# work being committed on the live checkout; an upstream merge is the one commit
# that BELONGS here. Without it the merge commit is refused, the merge is left
# half-open, and this script would report a content conflict that never existed.
MERGE_OUT="$(MARVEEN_PROD_COMMIT_OK=1 git merge --no-ff --no-edit "$REMOTE/$BRANCH" 2>&1)"
if [ $? -eq 0 ]; then
  NEW="$(git rev-parse --short HEAD)"
  state merged "behind=$BEHIND beolvasztva, HEAD=$NEW"
  notify "$(printf '%s\n' \
    "[UPSTREAM-SYNC] Beolvasztottam az upstream ${BEHIND} uj commitjat a helyi main-be (HEAD=${NEW})." \
    "A ket elozmeny ujra egy vonalon van, a frissites mar le tud futni." \
    "A rendszer meg a REGI, leforditott verzion fut -- a dist nem valtozott." \
    "Ha a gazda kéri, ./update.sh ujraforditja es ujrainditja (a sajat ongyogyito aga).")"
  echo "$TS merged $BEHIND upstream commits ($OLD -> $NEW)"
  exit 0
fi

# Merge failed. Unmerged paths mean a real content conflict; none means the
# merge was refused for some other reason (a hook, a lock, a broken index), and
# calling that a "conflict" would send the owner hunting for the wrong thing.
CONFLICTS="$(git diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ')"
git merge --abort >/dev/null 2>&1
NOW="$(git rev-parse HEAD)"
if [ "$NOW" != "$OLD" ]; then
  state error "MERGE ABORT NEM ALLT VISSZA: HEAD=$NOW volt=$OLD"
  notify "[UPSTREAM-SYNC] A merge elbukott ES az abort nem allitotta vissza a HEAD-et (${OLD} -> ${NOW}). Ezt nezd meg kezzel, ne inditsd a frissitest."
  echo "$TS ABORT DID NOT RESTORE $OLD -> $NOW" >&2
  exit 2
fi
if [ -n "$CONFLICTS" ]; then
  NEWSTATE="tartalmi utkozes: $CONFLICTS"
  # Quiet repeat, same rule as the blocked branch: same conflict set as last
  # tick -> state only, no wake-up (2026-10-14 it woke marveen every 15 min).
  if [ "$PREV_RESULT" = "conflict" ] && [ "$PREV_DETAIL" = "$NEWSTATE" ]; then
    state conflict "$NEWSTATE"
    echo "$TS CONFLICT (unchanged, not notified): $CONFLICTS" >&2
    exit 0
  fi
  state conflict "$NEWSTATE"
  notify "$(printf '%s\n' \
    "[UPSTREAM-SYNC] Az upstream ${BEHIND} uj commitja tartalmi utkozesbe fut a helyi munkaval." \
    "Erintett fajlok: $CONFLICTS" \
    "A merge visszavonva, a fa erintetlen. Ezt csak kezzel lehet feloldani, dontes kell hozza." \
    "Amig ez all, a frissites elbukik a pull fazisban.")"
  echo "$TS CONFLICT: $CONFLICTS" >&2
  exit 0
fi

REASON="$(printf '%s' "$MERGE_OUT" | tail -n 3 | tr '\n' ' ')"
state error "a merge nem futott le (nem utkozes): ${REASON:-ismeretlen}"
notify "$(printf '%s\n' \
  "[UPSTREAM-SYNC] Az upstream ${BEHIND} uj commitjat NEM tudtam beolvasztani, es NEM utkozes miatt." \
  "A git ezt mondta: ${REASON:-semmit}" \
  "A merge visszavonva, a fa erintetlen. Amig ez all, a frissites elbukik a pull fazisban.")"
echo "$TS MERGE FAILED (not a conflict): ${REASON:-unknown}" >&2
exit 0
