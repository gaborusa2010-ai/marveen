#!/usr/bin/env bash
# Guards what scripts/backup.sh takes out of agents/.
#
# 2026-09-06: rewritten to the CURRENT policy. The owner chose the full backup
# on 2026-09-05, so there is no image exclusion any more; the assertions below
# now say so. The paragraphs that follow are the history that makes the shape of
# the risk clear, and they are why the file is still here rather than deleted.
#
# 2026-09-22: THE PICTURES ARE EXCLUDED AGAIN, BUT NOT HERE, AND THE ASSERTIONS
# BELOW STAY AS THEY ARE. The owner reversed the 09-05 decision (Telegram msg
# 3536), and the pictures now live in their own archive. The exclusion is a
# SEPARATE block further down in backup.sh, and it subtracts only files that
# scripts/backup-images.sh can prove are already inside an uploaded image
# archive -- so this find still has to keep every one of them, exactly as the
# assertions say. If someone ever "simplifies" the new exclusion back into this
# find expression, the 321 kB of text and SVG described above goes with it, and
# this test is what says no. Covered from the other side by
# backup-differential.test.sh.
#
# Why it needs a guard. The exclusion started life as two -not -path lines,
# which is the obvious way to write "skip the generated pictures" and is wrong
# in a way nothing announces: measured 2026-09-04, it also dropped 37 text files
# and 40 SVGs, 321 kB, none of which was in git either. Among them the axolotl
# character bible -- the written brief and scene list a follow-up book has to
# match. The next person to simplify this back to a path exclusion would make
# the archive smaller by 321 kB and lose the only copy of that material, and the
# archive would look entirely healthy afterwards.
#
# The test runs backup.sh's OWN find expression, cut out of the file, rather
# than a second copy of it here. A test that reimplements the thing it checks
# goes green on an argument the real script never makes (card 6e07d9c2).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Overridable so the test can be pointed at a deliberately broken copy -- see
# the negative control note in the card. Defaults to the real script.
BACKUP_SH="${BACKUP_SH:-${REPO_ROOT}/scripts/backup.sh}"
PASS=0; FAIL=0

ok()   { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

echo "backup.sh: roxy exclusion is raster-only"
echo "=========================================="
echo

# Cut the agents/ find out of backup.sh and strip its redirection, so what runs
# here is the very text that runs in production.
EXPR="$(awk '/^  find agents -type f/,/-print >> "\$\{REPOLIST\}"/' "${BACKUP_SH}" \
        | sed 's|-print >> "\${REPOLIST}"|-print|')"

if [[ -z "${EXPR}" ]]; then
  bad "could not extract the agents/ find from backup.sh (did the block move?)"
  echo; echo "PASS: ${PASS}  FAIL: ${FAIL}"; exit 1
fi
ok "extracted the find expression from backup.sh itself"

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
mkdir -p "${TMP}/agents/roxy/work/covers" \
         "${TMP}/agents/roxy/characters/axolotls" \
         "${TMP}/agents/margo/memory"
: > "${TMP}/agents/roxy/work/covers/borito.png"
: > "${TMP}/agents/roxy/work/covers/BORITO-NAGY.JPG"     # upper case matters: -iname
: > "${TMP}/agents/roxy/work/ikonok.py"
: > "${TMP}/agents/roxy/work/ikon.svg"                    # vector source, text, tiny
: > "${TMP}/agents/roxy/characters/axolotls/ref-lime.png"
: > "${TMP}/agents/roxy/characters/axolotls/brief.txt"
: > "${TMP}/agents/margo/memory/MEMORY.md"

OUT="$(cd "${TMP}" && eval "${EXPR}" | sort)"

expect_in() {
  if grep -qx "$1" <<<"${OUT}"; then ok "kept: $1"; else bad "MISSING from the archive list: $1"; fi
}
expect_out() {
  if grep -qx "$1" <<<"${OUT}"; then bad "should NOT be archived: $1"; else ok "excluded: $1"; fi
}

# THE OWNER DECIDED, 2026-09-05 10:16: "Mentes." Card 7f486d35 offered three
# options and he took the whole one, so the raster exclusion is GONE and these
# three files are archived like everything else. Until 2026-09-06 this suite
# still asserted the opposite -- and it read as green because nothing ever ran
# it. That is the same shape as the bug it was written about: a check nobody
# executes and a gap nobody sees look identical from the outside.
expect_in  "agents/roxy/work/covers/borito.png"
expect_in  "agents/roxy/work/covers/BORITO-NAGY.JPG"
expect_in  "agents/roxy/characters/axolotls/ref-lime.png"

# The original finding still stands and is still worth guarding: when the
# exclusion DID exist it was written as two -not -path lines, which also dropped
# 37 text files and 40 SVGs (321 kB, none of it in git), the axolotl character
# bible among them. If anyone reintroduces a picture exclusion, it has to be
# raster-only -- these three must survive it.
expect_in  "agents/roxy/work/ikonok.py"
expect_in  "agents/roxy/work/ikon.svg"
expect_in  "agents/roxy/characters/axolotls/brief.txt"

# A raster OUTSIDE those two paths was never excluded and must stay taken.
mkdir -p "${TMP}/agents/cody/projects/app/assets"
: > "${TMP}/agents/cody/projects/app/assets/icon-192.png"
OUT="$(cd "${TMP}" && eval "${EXPR}" | sort)"
expect_in  "agents/cody/projects/app/assets/icon-192.png"
expect_in  "agents/margo/memory/MEMORY.md"

# Runtime pids are not content.
mkdir -p "${TMP}/agents/cody/.claude/channels/telegram"
: > "${TMP}/agents/cody/.claude/channels/telegram/bot.pid"
OUT="$(cd "${TMP}" && eval "${EXPR}" | sort)"
expect_out "agents/cody/.claude/channels/telegram/bot.pid"

echo
echo "======================="
echo "PASS: ${PASS}  FAIL: ${FAIL}"
[[ ${FAIL} -eq 0 ]]
