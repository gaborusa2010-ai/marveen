#!/usr/bin/env bash
# Guards the single-run lock on scripts/backup.sh and scripts/backup-images.sh.
#
# WHY IT EXISTS. Measured 2026-09-23: the image archive was built TWICE the same
# evening, 17 minutes apart (20:21 and 20:38), byte-identical in size, and
# neither run reached the upload. 6.4 GB of disk spent for zero uploads, and the
# state file still pointed at the previous day, so the next run would have
# started a third. The first hypothesis -- that the WSL layer died under it, as
# it had on 09-17, 09-21 and 09-22 (card 755110ea) -- was RULED OUT: the boot
# has been continuous since 09-22 17:16 (journalctl --list-boots). Nothing
# killed the first run. There was simply nothing stopping a second from starting
# beside it.
#
# WHAT THE LOCK MUST NOT DO, and this is the half a lock usually gets wrong:
#   - it must not block the READ-ONLY subcommands (status/list/covered). Those
#     are what a human or another script calls to ASK what would happen, and a
#     diagnostic that hangs behind a 3 GB build is worse than no diagnostic.
#   - it must not QUEUE. A second caller that waits its turn then rebuilds the
#     same archive has doubled the cost, only sequentially.
#   - it must not report FAILURE. "The work is already being done" is the
#     correct outcome; an alarm raised for it gets ignored, and the ignoring
#     spreads to the alarms that matter.
#   - it must not be SILENT. A skip nobody can see is how a backup stops running
#     without anyone noticing.
#
# The positive control is the one that matters here: a test that only proves
# "the second run stops" also passes against a script that never runs at all.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGES_SH="${IMAGES_SH:-${REPO_ROOT}/scripts/backup-images.sh}"
BACKUP_SH="${BACKUP_SH:-${REPO_ROOT}/scripts/backup.sh}"
PASS=0; FAIL=0
ok()  { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

TMPD="$(mktemp -d -t backup-lock-test.XXXXXX)"
trap 'rm -rf "${TMPD}"' EXIT

echo "backup lock: one run at a time, read-only paths stay free"
echo "========================================================="
echo

# --- 1. the lock is actually taken, and it is NON-BLOCKING -----------------
# A helper process holds the lock for the duration of the assertion; the script
# under test must come back immediately rather than wait for it.
hold_and_run() {  # $1=lockfile  $2..=command
  local lock="$1"; shift
  ( exec {FD}>"${lock}"; flock "${FD}"; sleep 8 ) &
  local holder=$!
  # Wait until the holder actually HAS the lock, so the race is decided before
  # the assertion rather than raced inside it. `flock -n <file> true` fails
  # exactly while someone else holds it, which is the condition we want.
  local i=0
  while [[ $i -lt 80 ]] && flock -n "${lock}" true 2>/dev/null; do
    sleep 0.1; i=$((i+1))
  done
  local start=$SECONDS out rc
  # EVERY call is wrapped in a timeout, and that is not belt-and-braces: if the
  # lock is broken, the command under test is a real multi-gigabyte backup. The
  # timeout is what keeps a FAILING test from running one.
  out="$(timeout 12 "$@" 2>&1)"; rc=$?
  ELAPSED=$((SECONDS-start))
  kill "${holder}" 2>/dev/null; wait "${holder}" 2>/dev/null
  OUT="${out}"; RC=${rc}
}

# The threshold is pinned absurdly high for every call below. The lock is what
# is under test here, not the rebuild decision -- and without this, the
# uncontended positive control would build the real 3 GB archive to prove it.
export IMAGE_REBUILD_MB=99999999

IMG_LOCK="${TMPD}/img.lock"
hold_and_run "${IMG_LOCK}" env IMAGE_LOCK_FILE="${IMG_LOCK}" \
  IMAGE_STATE_FILE="${TMPD}/img-state.json" IMAGE_BACKUP_DIR="${TMPD}/out" \
  bash "${IMAGES_SH}" run

[[ ${RC} -eq 0 ]] && ok "images: contended run exits 0, not failure" \
                  || bad "images: contended run exited ${RC}, expected 0"
[[ ${ELAPSED} -lt 4 ]] && ok "images: contended run returns immediately (${ELAPSED}s), does not queue" \
                       || bad "images: contended run waited ${ELAPSED}s -- the lock is blocking"
grep -qi "MAR FUT" <<<"${OUT}" && ok "images: the skip is announced, not silent" \
                               || bad "images: skipped silently -- output was: ${OUT}"

# --- 2. POSITIVE CONTROL: without contention the same call proceeds ---------
# Without this, every assertion above would also pass against a script that
# refuses to do anything at all.
OUT2="$(timeout 12 env IMAGE_LOCK_FILE="${TMPD}/free.lock" \
        IMAGE_STATE_FILE="${TMPD}/img-state.json" IMAGE_BACKUP_DIR="${TMPD}/out" \
        bash "${IMAGES_SH}" run 2>&1)"
if grep -qi "MAR FUT" <<<"${OUT2}"; then
  bad "images: POSITIVE CONTROL -- an uncontended run was skipped anyway"
else
  ok "images: POSITIVE CONTROL -- an uncontended run is not skipped"
fi

# --- 3. the read-only subcommands are NOT gated by the lock -----------------
# EACH ONE IS COMPARED AGAINST ITS OWN UNCONTENDED BASELINE, not against a flat
# number of seconds. `status` fingerprints all 1682 files and legitimately takes
# a few seconds; a fixed threshold called that a blocked lock on the first run
# of this test (measured 4s), which is the test being wrong about the code. The
# question is not "was it fast" but "did contention make it slower".
for sub in status list covered; do
  BASE_START=$SECONDS
  timeout 30 env IMAGE_LOCK_FILE="${TMPD}/free2.lock" \
    IMAGE_STATE_FILE="${TMPD}/img-state.json" IMAGE_BACKUP_DIR="${TMPD}/out" \
    bash "${IMAGES_SH}" "${sub}" >/dev/null 2>&1
  BASELINE=$((SECONDS-BASE_START))

  hold_and_run "${IMG_LOCK}" env IMAGE_LOCK_FILE="${IMG_LOCK}" \
    IMAGE_STATE_FILE="${TMPD}/img-state.json" IMAGE_BACKUP_DIR="${TMPD}/out" \
    bash "${IMAGES_SH}" "${sub}"

  if grep -qi "MAR FUT" <<<"${OUT}"; then
    bad "images: '${sub}' was SKIPPED by the build lock -- read-only paths must not be gated"
  elif [[ ${ELAPSED} -gt $((BASELINE + 3)) ]]; then
    bad "images: '${sub}' waited on the lock (${ELAPSED}s vs ${BASELINE}s uncontended)"
  else
    ok "images: '${sub}' runs while a build holds the lock (${ELAPSED}s vs ${BASELINE}s baseline)"
  fi
done

# --- 4. the nightly backup takes its lock from BACKUP_DIR -------------------
# So a test (or a one-off restore check) with its own BACKUP_DIR cannot block
# the real nightly run, and cannot be blocked by it.
BK_DIR="${TMPD}/bk"; mkdir -p "${BK_DIR}"
hold_and_run "${BK_DIR}/.backup.lock" env BACKUP_DIR="${BK_DIR}" BACKUP_MODE=diff bash "${BACKUP_SH}"
[[ ${RC} -eq 0 ]] && ok "backup: contended run exits 0" \
                  || bad "backup: contended run exited ${RC}, expected 0"
[[ ${ELAPSED} -lt 4 ]] && ok "backup: contended run returns immediately (${ELAPSED}s)" \
                       || bad "backup: contended run waited ${ELAPSED}s"
grep -qi "MAR FUT" <<<"${OUT}" && ok "backup: the skip is announced" \
                               || bad "backup: skipped silently -- output was: ${OUT}"

# The lock file must sit under BACKUP_DIR, not the repo root -- otherwise the
# isolation the line above claims is not real.
[[ -e "${BK_DIR}/.backup.lock" ]] && ok "backup: lock file lives under BACKUP_DIR" \
                                  || bad "backup: no lock file under BACKUP_DIR -- isolation is not real"

echo
echo "======================="
echo "PASS: ${PASS}  FAIL: ${FAIL}"
[[ ${FAIL} -eq 0 ]]
