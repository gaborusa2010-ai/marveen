#!/usr/bin/env bash
# Guards the repo-root sweep in scripts/backup.sh.
#
# WHY IT NEEDS A GUARD. Until 2026-09-06 the repo root was an allowlist of
# names, and the names nobody wrote down were the product's own source. Measured
# that afternoon against the 09:46 archive: 38 files under src/, 3 under docs/
# and 3 under seed-skills/ were in NO copy at all -- not the archive, not a
# clean git state. Sixteen of the src ones were test files written this week and
# never committed. The daily sentinel had been printing those three lines every
# morning without anything changing.
#
# The fix inverts the root the same way agents/ was inverted on 2026-09-04:
# everything is taken, an exclusion has to be written down. The failure mode of
# an inverted list is the opposite one -- it can quietly start swallowing
# node_modules, dist or the 491 MB agents/ tree, and the only symptom is a
# bigger archive. So this test asserts BOTH directions.
#
# Like backup-roxy-exclusion.test.sh, it runs backup.sh's OWN block, cut out of
# the file. A test that reimplements the thing it checks goes green on an
# argument the real script never makes.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_SH="${BACKUP_SH:-${REPO_ROOT}/scripts/backup.sh}"
PASS=0; FAIL=0

ok()  { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

echo "backup.sh: the repo root is swept, not listed"
echo "=============================================="
echo

BLOCK="$(awk '/^while IFS= read -r rootentry; do/,/^done < <\(find \. -maxdepth 1/' "${BACKUP_SH}")"
if [[ -z "${BLOCK}" ]]; then
  bad "could not extract the repo-root sweep from backup.sh (did the block move?)"
  echo; echo "PASS: ${PASS}  FAIL: ${FAIL}"; exit 1
fi

FIX="$(mktemp -d -t backup-sweep-fixture.XXXXXX)"
LIST="$(mktemp -t backup-sweep-list.XXXXXX)"
trap 'rm -rf "${FIX}" "${LIST}"' EXIT

# A miniature repo with one file in every interesting place.
mkdir -p "${FIX}"/{src/__tests__,docs,seed-skills/uj-skill,web,tests,.github}
mkdir -p "${FIX}"/{node_modules/pkg,dist,dist.bak-20260101-0000,backups,coverage,logs,.venv}
mkdir -p "${FIX}"/{.git,store/big,agents/cody,scripts/hooks,templates,.claude}
mkdir -p "${FIX}"/src/__pycache__ "${FIX}"/web/node_modules/dep
: > "${FIX}/src/db.ts"
: > "${FIX}/src/__tests__/new.test.ts"
: > "${FIX}/docs/reference.md"
: > "${FIX}/seed-skills/uj-skill/SKILL.md"
: > "${FIX}/web/app.js"
: > "${FIX}/tests/smoke.spec.ts"
: > "${FIX}/.github/workflow.yml"
: > "${FIX}/package.json"
: > "${FIX}/update.sh"
: > "${FIX}/HANDOFF.md"
: > "${FIX}/CLAUDE.md"
: > "${FIX}/SOUL.md"
: > "${FIX}/.env"
: > "${FIX}/scheduled-tasks.json"
: > "${FIX}/node_modules/pkg/index.js"
: > "${FIX}/dist/bundle.js"
: > "${FIX}/dist.bak-20260101-0000/bundle.js"
: > "${FIX}/backups/old.tar.gz"
: > "${FIX}/coverage/lcov.info"
: > "${FIX}/logs/run.log"
: > "${FIX}/.venv/pyvenv.cfg"
: > "${FIX}/.git/HEAD"
: > "${FIX}/store/big/blob.bin"
: > "${FIX}/agents/cody/CLAUDE.md"
: > "${FIX}/scripts/hooks/gate.mjs"
: > "${FIX}/templates/settings.json.template"
: > "${FIX}/.claude/settings.json"
: > "${FIX}/src/__pycache__/x.pyc"
: > "${FIX}/src/db.ts.bak-20260101"
: > "${FIX}/web/node_modules/dep/index.js"
: > "${FIX}/web/bridge.pid"

REPOLIST="${LIST}"
export REPOLIST
( cd "${FIX}" && REPOLIST="${LIST}" bash -c "${BLOCK}" )

has()  { grep -qxF "$1" "${LIST}"; }

# --- Direction 1: the things that had no copy must now be taken. ------------
for want in src/db.ts src/__tests__/new.test.ts docs/reference.md \
            seed-skills/uj-skill/SKILL.md web/app.js tests/smoke.spec.ts \
            .github/workflow.yml package.json update.sh HANDOFF.md; do
  if has "${want}"; then ok "taken: ${want}"; else bad "MISSING from the sweep: ${want}"; fi
done

# --- Direction 2: an inverted list must not swallow the big or the handled. -
for unwanted in node_modules/pkg/index.js dist/bundle.js \
                dist.bak-20260101-0000/bundle.js backups/old.tar.gz \
                coverage/lcov.info logs/run.log .venv/pyvenv.cfg \
                .git/HEAD store/big/blob.bin agents/cody/CLAUDE.md \
                scripts/hooks/gate.mjs templates/settings.json.template \
                .claude/settings.json CLAUDE.md SOUL.md .env scheduled-tasks.json; do
  if has "${unwanted}"; then bad "SWALLOWED (excluded or already taken above): ${unwanted}"; else ok "skipped: ${unwanted}"; fi
done

# --- Direction 2b: the per-file exclusions inside a swept directory. --------
for noise in src/__pycache__/x.pyc src/db.ts.bak-20260101 \
             web/node_modules/dep/index.js web/bridge.pid; do
  if has "${noise}"; then bad "SWALLOWED (runtime/build noise): ${noise}"; else ok "skipped: ${noise}"; fi
done

# --- Against the REAL repo: the three directories from the finding. ---------
LIVE="$(mktemp -t backup-sweep-live.XXXXXX)"
( cd "${REPO_ROOT}" && REPOLIST="${LIVE}" bash -c "${BLOCK}" )
for d in src docs seed-skills; do
  if grep -q "^${d}/" "${LIVE}"; then ok "real repo: ${d}/ is in the list"; else bad "real repo: ${d}/ still absent"; fi
done
if grep -q "^agents/" "${LIVE}" || grep -q "^node_modules/" "${LIVE}"; then
  bad "real repo: the sweep reached agents/ or node_modules/ (archive would balloon)"
else
  ok "real repo: agents/ and node_modules/ stay out of the sweep"
fi
rm -f "${LIVE}"

echo
echo "PASS: ${PASS}  FAIL: ${FAIL}"
[[ ${FAIL} -eq 0 ]]
