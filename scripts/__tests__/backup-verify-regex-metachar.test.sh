#!/usr/bin/env bash
# Guards the manifest-vs-archive match in scripts/backup.sh against regex
# metacharacters in file names.
#
# 2026-09-10 20:01, live run: three compiled Java classes under
# agents/cody/projects/worklog-android-proof/build/ -- MainActivity$1.class,
# MainActivity$2.class, R$string.class -- were IN the archive (tar -tzf listed
# all three) and the verifier still printed "MISSING" for each and exited 6.
# The manifest line was fed to grep -E as a pattern, so the "$" became a line
# anchor and could never match. A verifier that fails on a good archive is the
# mirror of the 09-04 shape (a verifier that passes a useless one): both make
# the exit code answer a different question than "is the copy good".
#
# The test cuts the match block out of backup.sh between its own markers and
# runs THAT text, not a re-implementation (same reason as the sibling tests:
# card 6e07d9c2). Overridable BACKUP_SH so the negative control can point at a
# copy with the old, unescaped line.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_SH="${BACKUP_SH:-${REPO_ROOT}/scripts/backup.sh}"
PASS=0; FAIL=0
ok()  { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

echo "backup.sh: manifest match survives regex metacharacters"
echo "======================================================="
echo

BLOCK="$(sed -n '/# verify-match-begin/,/# verify-match-end/p' "${BACKUP_SH}" | grep -v '^ *#')"
[[ -n "${BLOCK}" ]] || { bad "no verify-match block found in ${BACKUP_SH}"; echo; echo "PASS=${PASS} FAIL=${FAIL}"; exit 1; }
# The block ends with an open "if ! grep ...; then". Close it into a function
# that prints PRESENT or MISSING so the test can assert on it.
present() {
  local want="$1"
  eval "${BLOCK}
    echo MISSING
  else
    echo PRESENT
  fi"
}

ARCHIVE_LIST="$(mktemp -t backup-verify-test.XXXXXX)"
trap 'rm -f "${ARCHIVE_LIST}"' EXIT
cat > "${ARCHIVE_LIST}" <<'LIST'
repo/agents/cody/projects/proof/build/classes/MainActivity$2.class
repo/agents/cody/projects/proof/build/classes/R$string.class
repo/store/x
repo/store/xyz
repo/docs/notes (draft).md
home/.claude/skills/
home/.claude/skills/a/SKILL.md
LIST

t() { # expected want
  local exp="$1" want="$2" got
  got="$(present "${want}")"
  [[ "${got}" == "${exp}" ]] && ok "${want} -> ${got}" || bad "${want} -> ${got} (expected ${exp})"
}
t PRESENT 'repo/agents/cody/projects/proof/build/classes/MainActivity$2.class'
t PRESENT 'repo/agents/cody/projects/proof/build/classes/R$string.class'
t MISSING 'repo/agents/cody/projects/proof/build/classes/MainActivity$3.class'
t PRESENT 'repo/docs/notes (draft).md'
t PRESENT 'repo/store/x'
t MISSING 'repo/store/xy'
t PRESENT 'home/.claude/skills'
t MISSING 'home/.claude/skill'
# A dot must stay literal: "repo/store/x" listed, "repo/storeXx" asked.
t MISSING 'repo/storeXx'

echo
echo "PASS=${PASS} FAIL=${FAIL}"
[[ "${FAIL}" -eq 0 ]]
