#!/usr/bin/env bash
# Marveen backup.
#
# The archive has two top-level groups so a restore is unambiguous about
# where each file belongs (see docs/MIGRATION.md):
#
#   repo/   -> extract under the project root (this repo)
#     store/claudeclaw.db (+ -shm/-wal; WAL-checkpointed before copy)
#     store/.dashboard-token   (dashboard bearer)
#     .env                     (project root secrets)
#     scheduled-tasks.json     (legacy, if present)
#     assets/meetings/**       (meeting transcripts/memos)
#     agents/*/CLAUDE.md, SOUL.md, .mcp.json
#     agents/*/.claude/channels/{telegram,slack,discord}/.env, access.json
#
#   home/   -> extract under $HOME
#     .claude/skills/**            (the self-built skill library)
#     .claude/scheduled-tasks/**   (file-based scheduled tasks: SKILL.md + config)
#     .claude/channels/*/.env      (MAIN orchestrator channel token)
#     .claude/channels/*/access.json, invites.json, approved/**  (pairing state)
#     Library/LaunchAgents/com.<MAIN_AGENT_ID>.*.plist (launchd jobs)
#
# Output: backups/claudeclaw-YYYYmmdd-HHMMSS.tar.gz
# Retention: keeps the most recent 14 archives, prunes the rest.
#
# Restore (preserve modes so the 0600 token files stay private):
#   tar -xpzf <archive> -C /tmp/restore        # inspect first
#   then copy repo/* into the project root and home/* into $HOME.
# Full runbook: docs/MIGRATION.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${REPO_ROOT}/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="${BACKUP_DIR}/claudeclaw-${STAMP}.tar.gz"
KEEP=14

mkdir -p "${BACKUP_DIR}"
cd "${REPO_ROOT}"

# Checkpoint WAL into the main DB file so the snapshot is self-contained.
# Tolerate a missing sqlite3 CLI -- just fall back to copying the files as-is.
if [[ -f store/claudeclaw.db ]] && command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 store/claudeclaw.db 'PRAGMA wal_checkpoint(TRUNCATE);' >/dev/null || true
fi

# --- Build the two path lists (each relative to its own base). -------------
# tar refuses missing entries, which would fail the whole backup on a fresh
# machine (no agents yet) -- so we only list paths that actually exist.
REPOLIST="$(mktemp -t claudeclaw-repo.XXXXXX)"
HOMELIST="$(mktemp -t claudeclaw-home.XXXXXX)"
MANIFEST="$(mktemp -t claudeclaw-manifest.XXXXXX)"
STAGE="$(mktemp -d -t claudeclaw-stage.XXXXXX)"
trap 'rm -f "${REPOLIST}" "${HOMELIST}" "${MANIFEST}"; rm -rf "${STAGE}"' EXIT

# add_if <listfile> <base> <relpath>  -- append relpath when <base>/<relpath> exists.
add_if() {
  local list="$1" base="$2" rel="$3"
  if [[ -e "${base}/${rel}" ]]; then echo "${rel}" >> "${list}"; fi
}

# repo/ group (relative to REPO_ROOT)
add_if "${REPOLIST}" "${REPO_ROOT}" store/claudeclaw.db
add_if "${REPOLIST}" "${REPO_ROOT}" store/claudeclaw.db-shm
add_if "${REPOLIST}" "${REPO_ROOT}" store/claudeclaw.db-wal
add_if "${REPOLIST}" "${REPO_ROOT}" store/.dashboard-token
add_if "${REPOLIST}" "${REPO_ROOT}" store/config-overrides.json
add_if "${REPOLIST}" "${REPO_ROOT}" .env
add_if "${REPOLIST}" "${REPO_ROOT}" scheduled-tasks.json
add_if "${REPOLIST}" "${REPO_ROOT}" assets/meetings
# The owner's own tooling (work log, invoicing, cutting list, receipts, Google
# auth). It lives in the repo but was untracked for three days, so neither git
# nor this archive held a copy -- a disk failure would have taken all of it.
# It is tracked now; keeping it here too means a restore does not depend on the
# git history being intact.
add_if "${REPOLIST}" "${REPO_ROOT}" scripts/muhely
# 2026-09-03: the line above took ONE named subdirectory, so the archive contained a
# repo/scripts entry and still held nothing but muhely -- which is worse than an obvious
# gap, because the directory name in the listing reads as coverage. Measured that day
# (Forgacs): scripts/hooks in the newest archive -> 0 entries, while 3 of the hooks are
# untracked by git as well (bash-egress-gate.mjs and global-skill-write-gate.mjs among
# them, both live security gates) and the branch was 58 commits ahead of any remote.
# So neither git nor the backup held them. scripts/ is 2.8 MB; taking all of it costs
# nothing and removes the question of which subdirectory someone remembered to list.
add_if "${REPOLIST}" "${REPO_ROOT}" scripts
# ...and the DATA those scripts run on, which was the other half of the same gap
# (found 2026-08-24). store/ is gitignored, and only the db and the token were
# listed above, so the invoice counter, the business details and every job folder
# (cutting lists, nesting plans, generators, drawing photos) had NO copy anywhere.
# Losing muhely-counters.json alone would break the continuous invoice sequence
# that HMRC requires.
# A gazda 2026-09-05-i dontese ("Mentes") ide is szol: a roxy-proba a store
# alatt all, tehat a fenti agents/ find nem latja. 21 fajl, 25 MB.
# Anorak YouTube-munkalistai es atiratai. A munkalista a 753 videos elemzes
# HALADAS-NYILVANTARTASA (legregebbitol az ujig rendezve, a gazda 2026-09-04-i
# keresere) -- ha elvesz, a sorrend es a mar feldolgozott resz is elvesz vele.
# 4 fajl, 104 kB. A backup-coverage.py fogta meg 2026-09-05-en, uj mappakent.
# A KAPUK SAJAT SZABALYFAJLJAI ES A CEG-NYILVANTARTAS. 2026-09-05-en derult ki,
# hogy egyik sincs a mentesben, es a hianyuk NEM egy funkciot vinne el, hanem
# nemán levenne egy vedelmet -- ugyanaz az ervelés, amivel a user-szintu
# settings.json bekerult lentebb. A muhely-invoice-log a FOLYAMATOS
# szamla-sorszamozas nyilvantartasa, tehat HMRC-adat, nem konfig.
# Egyutt 100 kB. A `store/` egesze 81 MB, annak nagyja log es .bak.
# A GIT-TORTENET MAGA. A gazda dontese 2026-09-06 09:45 ("b"), es ez az elso alkalom,
# hogy a mentes nem fajlokat vesz be, hanem a TORTENETUKET.
# A meres, ami a dontest kivaltotta (2026-09-06 09:40): 58 commit letezett KIZAROLAG
# ezen a lemezen -- nem volt elerheto sem az origin/main-rol, sem a fork/main-rol, sem
# semmilyen tavoli refbol. Az osszes helyi agat nezve 114. A legregebbi ilyen commit
# 2026-08-23-i, es valodi munkat tartalmaz (kanban blokkolo-mezo, fetch-budget javitas,
# arveres-figyelo, munkanaplo cim-szerkesztes).
# Miert a .git es nem a felkuldes: az origin idegen upstream (nincs push-jogunk), a fork
# pedig PUBLIC -- a 58 commit ket helyen tartalmaz szemelyes adatot (beegetett Telegram
# chat_id, illetve egy Drive-mappa neve a gazda teljes nevevel). A titkositott mentes
# ezt a ket kerdest egyszerre oldja meg: nem megy ki semmi a gepről.
# Ara: a .git 46 MB, a mostani archivum 513 MB -- kb. plusz 9 szazalek.
# FIGYELEM egy kesobbi olvasonak: ez a sor a teljes TORTENETET viszi, nem a munkafat.
# Egy visszaallitas utan a munkafa allapotat a `git checkout` allitja helyre; a
# nem-commitolt valtozasok NINCSENEK benne (azokat a fenti fajl-listak fogjak).
add_if "${REPOLIST}" "${REPO_ROOT}" .git
add_if "${REPOLIST}" "${REPO_ROOT}" store/egress-allowlist.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/outgoing-copy-gate-rules.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/provenance-gate-rules.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/skill-write-grants.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/skill-write-grant.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/muhely-invoice-log.json
# A gazda sajat anyagai es a flotta-konfig, amit ujra kitalalni kellene:
# A store gyokereben heverő regi briefek (a store/briefs mappa elotti idokbol).
# Findvel, hogy egy uj brief se maradjon ki csendben.
find store -maxdepth 1 -name 'brief-*.md' -type f -print >> "${REPOLIST}" 2>/dev/null || true
add_if "${REPOLIST}" "${REPO_ROOT}" store/ikigai-kerdesek.md
add_if "${REPOLIST}" "${REPO_ROOT}" store/agents-desired.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/auto-restart.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/context-guard.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/gpu-watch.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/usage-watch.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/usage-history.jsonl
add_if "${REPOLIST}" "${REPO_ROOT}" store/yt-munkalista
add_if "${REPOLIST}" "${REPO_ROOT}" store/yt-atiratok
add_if "${REPOLIST}" "${REPO_ROOT}" store/roxy-proba
add_if "${REPOLIST}" "${REPO_ROOT}" store/muhely-business.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/muhely-counters.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/muhely-jobs.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/autonomy-config.json
add_if "${REPOLIST}" "${REPO_ROOT}" store/muhely
# Owner-supplied and hand-made content under store/. All four were measured OUT
# of the archive on 2026-09-04 (scripts/backup-coverage.py) and none of them is
# reproducible from anything else on disk:
#   idea-attachments  -- what the OWNER put in the idea box; his data, not ours
#   munkanaplo-ikonok -- the worklog app's icon set
#   brand             -- the fleet's brand assets
#   profilkepek       -- the agents' portraits; regenerable in principle, but
#                        they cost image-generation quota and were chosen once
add_if "${REPOLIST}" "${REPO_ROOT}" store/idea-attachments
add_if "${REPOLIST}" "${REPO_ROOT}" store/munkanaplo-ikonok
add_if "${REPOLIST}" "${REPO_ROOT}" store/brand
add_if "${REPOLIST}" "${REPO_ROOT}" store/profilkepek
# The MAIN agent's own identity files. Found missing 2026-08-27 while trimming
# CLAUDE.md: both are gitignored (they carry the owner's name and chat id, so they
# are deliberately not in the public repo) AND the list below only globs under
# agents/, so the root pair had NO copy anywhere. Forgacs's CLAUDE.md was in the
# archive and the main agent's was not. Same class as the 2026-08-24 gap, one level
# up: the thing that defines how this agent behaves was the thing least protected.
add_if "${REPOLIST}" "${REPO_ROOT}" CLAUDE.md
add_if "${REPOLIST}" "${REPO_ROOT}" SOUL.md
# The fleet-agent scaffolding templates: what every NEW agent is built from.
# Three files, and the sibling *.template files next to them are already tracked
# by git -- these are not. Measured out of the archive 2026-09-04.
add_if "${REPOLIST}" "${REPO_ROOT}" templates
# Saved YouTube analyses (transcripts + findings). 204 kB, and re-fetching them
# would mean going back to the same hosts -- see the fetch-budget rule.
add_if "${REPOLIST}" "${REPO_ROOT}" store/youtube-watch
# Written briefs handed to agents at start-up. One file today; the reason it is
# named here rather than left to chance is the same as everywhere above -- a
# directory nobody lists stays invisible however small it is.
add_if "${REPOLIST}" "${REPO_ROOT}" store/briefs

# EVERYTHING under agents/, minus a named few. This block used to be five
# separate find(1) calls, each naming one thing worth keeping -- identity files,
# then memory/, then reports/, then projects/, then per-agent skills -- and every
# one of those lines was added AFTER something turned out to be missing. That is
# the shape of the bug, not a series of oversights: an allowlist makes every NEW
# directory invisible by default, and nothing announces it. Measured 2026-09-04
# with scripts/backup-coverage.py, after the fourth such line had just been
# added: margo/tervek, margo/bejovo, cody/handoff and store/briefs were STILL
# outside, and they only surfaced because a tool went looking. The next new
# folder would have been the fifth.
#
# So the list is inverted. Everything is taken; an exclusion has to be written
# down, with its reason, and is therefore a decision someone can argue with:
#   (nincs tobb kep-kizaras)  -- A GAZDA DONTOTT, 2026-09-05 10:16: "Mentes."
#                               A kartya (7f486d35) harom utat kinalt; o a
#                               teljeset valasztotta. Ezert a raszter-kizaras
#                               MEGSZUNT: agents/roxy/work es */characters
#                               kepei mostantol BENNE vannak, es a
#                               store/roxy-proba is (kulon add_if lentebb).
#                               MERVE a dontes napjan: 452 MB + 5,9 MB + 25 MB
#                               = 483 MB, az archivum kb. 30 -> 510 MB, 14
#                               megtartott peldannyal kb. 7 GB a Drive-on.
#                               A kvota megmerve ugyanakkor: 54,5 / 214,7 GB
#                               hasznalt, 160 GB szabad. Elfer.
#   .claude-config           -- per-agent plugin/cache tree, reinstalled
#   inbox/, progress/        -- channel runtime: raw attachments and seen-markers
#   *.pid                    -- channel runtime too: four 7-byte process ids that
#                               are wrong the moment the bot restarts. Named here
#                               because backup-coverage.py otherwise reports
#                               .claude/channels as a permanent gap, and a checker
#                               that is always red is a checker nobody reads.
#   __pycache__, node_modules -- build output
#   *.bak-*                  -- copies of files that are themselves taken
if [[ -d agents ]]; then
  find agents -type f \
    -not -path '*/.claude-config/*' \
    -not -path '*/inbox/*' \
    -not -path '*/progress/*' \
    -not -path '*/__pycache__/*' \
    -not -path '*/node_modules/*' \
    -not -name '*.bak-*' \
    -not -name '*.pid' \
    -print >> "${REPOLIST}"
fi

# The PROJECT's own Claude configuration: settings.json (which wires up the
# hooks), its skills, and its sub-agent definitions. The user-level twin
# (~/.claude/settings.json) has a backup line below and this one had none --
# the same "two settings.json files, one of them forgotten" shape the fleet has
# already been bitten by once. 13 files, 180 kB, in no backup and in no git.
if [[ -d .claude ]]; then
  find .claude -type f -not -name '*.bak-*' -print >> "${REPOLIST}"
fi

# home/ group (relative to $HOME)
add_if "${HOMELIST}" "${HOME}" .claude/skills
add_if "${HOMELIST}" "${HOME}" .claude/scheduled-tasks
# THE USER-LEVEL settings.json: this is where every PreToolUse / PostToolUse
# hook in the fleet is wired up -- the egress gate, the shared-skill brake, the
# write audit. Losing it does not lose a feature, it silently removes every
# guard at once, and nothing would announce that. It was in no backup until
# 2026-09-04. Named explicitly rather than taking the whole .claude root, which
# also holds credentials, a large history.jsonl and assorted caches.
add_if "${HOMELIST}" "${HOME}" .claude/settings.json
# ...and the scripts that settings.json POINTS AT. Backing up the wiring while
# leaving the wired-up files behind restores a configuration that references
# things that are gone -- worse than an empty config, because it looks intact.
# Six hook scripts (the Telegram progress/fallback set, the channel image
# resizer), the sub-agent definitions, and the threat-intel data the bumblebee
# scan reads. 420 kB together.
add_if "${HOMELIST}" "${HOME}" .claude/hooks
add_if "${HOMELIST}" "${HOME}" .claude/agents
add_if "${HOMELIST}" "${HOME}" .claude/tools
# The auto-memory folder the fleet ACTUALLY reads. The find over agents/ above
# looks like it covers this and does not: an agent's .claude-config/projects/...
# path is a symlink (find without -L will not descend it) and the real directory
# lives outside the agents/ tree entirely. Measured 2026-09-03 (Margo found it,
# verified here): that find returns 39 files and ZERO of them from here, while
# this directory holds 41 .md / 180 kB. It is in no git repo either, so an
# overwrite there is silent AND unrecoverable -- and on that same morning two
# agents wrote the same file three minutes apart with `cat >` heredocs, where
# only the ordering kept one of them from erasing the other.
# The bitter half: the backup was preserving agents/<name>/memory/, which the
# agents largely do not load, and skipping the one they all share.
# Only memory/ is taken; the sibling .jsonl transcripts are runtime bulk.
if [[ -d "${HOME}/.claude/projects" ]]; then
  ( cd "${HOME}" && find .claude/projects -mindepth 2 -maxdepth 2 -type d -name memory -print ) >> "${HOMELIST}"
fi
# MAIN orchestrator channel tokens + pairing state, per provider. bot.pid and
# inbox/ are runtime/transient and intentionally excluded.
if [[ -d "${HOME}/.claude/channels" ]]; then
  ( cd "${HOME}" && find .claude/channels -maxdepth 2 \
      \( -name '.env' -o -name 'access.json' -o -name 'invites.json' \) \
      -print ) >> "${HOMELIST}"
  ( cd "${HOME}" && find .claude/channels -maxdepth 2 -type d -name 'approved' -print ) >> "${HOMELIST}"
fi
# launchd jobs for this fleet. The job labels are com.<MAIN_AGENT_ID>.<service>
# (see src/web/main-agent.ts), so resolve MAIN_AGENT_ID the way the app does
# (src/env.ts: read from .env, default "marveen" when unset) instead of
# hardcoding one deployment's prefix. Parsing mirrors env.ts: last definition
# wins, surrounding matching quotes stripped.
MAIN_AGENT_ID="marveen"
if [[ -f "${REPO_ROOT}/.env" ]]; then
  # `|| true`: with `set -o pipefail`, a no-match grep would otherwise fail the
  # whole substitution (and, under `set -e`, abort the backup) on any install
  # that leaves MAIN_AGENT_ID unset and relies on the "marveen" default.
  _mid="$(grep -E '^[[:space:]]*MAIN_AGENT_ID[[:space:]]*=' "${REPO_ROOT}/.env" | tail -1 \
    | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*$//; s/^"(.*)"$/\1/; s/^'\''(.*)'\''$/\1/' || true)"
  [[ -n "${_mid}" ]] && MAIN_AGENT_ID="${_mid}"
fi
if [[ -d "${HOME}/Library/LaunchAgents" ]]; then
  ( cd "${HOME}" && find Library/LaunchAgents -maxdepth 1 -name "com.${MAIN_AGENT_ID}.*.plist" -print ) >> "${HOMELIST}"
fi

if [[ ! -s "${REPOLIST}" && ! -s "${HOMELIST}" ]]; then
  echo "backup: nothing to archive" >&2
  exit 0
fi

# --- Manifest (stored at the archive root for self-description). -----------
{
  echo "Marveen backup ${STAMP}"
  echo "host: $(hostname 2>/dev/null || echo '?')   user: ${USER:-?}   home: ${HOME}"
  echo "repo root: ${REPO_ROOT}"
  echo "Restore: tar -xpzf <archive> -C <tmp>; copy repo/* -> project root, home/* -> \$HOME."
  echo "See docs/MIGRATION.md for the full runbook (TCC, launchd paths, one-bot-one-poller, venv rebuild)."
  echo "--- repo/ ---"; sed 's,^,repo/,' "${REPOLIST}" 2>/dev/null || true
  echo "--- home/ ---"; sed 's,^,home/,' "${HOMELIST}" 2>/dev/null || true
} > "${MANIFEST}"

# --- Assemble the archive via a staging dir, then one plain tar. -----------
# The repo/ and home/ groups are produced by copying into a staging tree, NOT
# by tar name-substitution: bsdtar's `-s` and GNU tar's `--transform` are
# mutually incompatible (on GNU tar, `-s` is `--same-order` and takes no
# argument), so a substitution-based build is not portable. Staging + a single
# `tar -czf -C "${STAGE}" .` works identically on macOS (bsdtar) and Linux
# (GNU tar). Everything backed up is small (a few MB), so the copy is cheap;
# `cp -pR` preserves modes so the 0600 token files stay private.
cp "${MANIFEST}" "${STAGE}/MANIFEST.txt"

# A local copy of the same manifest, so "what did the last backup actually
# take?" is answerable WITHOUT unpacking (and decrypting) an archive.
#
# Why this one line exists: on 2026-09-03 four separate things turned out to be
# missing from the backup in a single morning, and the lateness watchdog said
# RENDBEN throughout -- because it measures the AGE of the last upload, not its
# CONTENTS. The list is built here from the real thing, so a checker reading
# this file cannot drift from what was archived; a second copy of the list-
# building logic would have become wrong the first time either half changed.
# Read it with scripts/backup-coverage.py.
cp "${MANIFEST}" "${REPO_ROOT}/store/.last-backup-manifest.txt" 2>/dev/null || true

stage_group() {  # stage_group <listfile> <base> <group>
  local list="$1" base="$2" group="$3" rel parent
  [[ -s "${list}" ]] || return 0
  while IFS= read -r rel; do
    [[ -z "${rel}" ]] && continue
    parent="$(dirname "${rel}")"
    mkdir -p "${STAGE}/${group}/${parent}"
    cp -pR "${base}/${rel}" "${STAGE}/${group}/${parent}/"
  done < "${list}"
}

stage_group "${REPOLIST}" "${REPO_ROOT}" repo
stage_group "${HOMELIST}" "${HOME}" home

# Archive only the top-level entries that exist (a group dir is absent when
# its list was empty), so tar never errors on a missing entry and the names
# stay clean (no leading "./").
( cd "${STAGE}" && tar -czf "${ARCHIVE}" MANIFEST.txt \
    $( [[ -d repo ]] && echo repo ) $( [[ -d home ]] && echo home ) )
echo "backup: wrote ${ARCHIVE} ($(wc -c < "${ARCHIVE}" | awk '{print $1}') bytes)"

# The archive contains sensitive tokens (dashboard bearer, channel bot tokens,
# project .env secrets). Do not auto-sync ${BACKUP_DIR} to iCloud, Dropbox,
# Google Drive, or any other cloud-backup folder. Keep it local.
echo "backup: WARNING -- archive contains sensitive tokens; keep ${BACKUP_DIR} out of cloud-sync folders (iCloud / Dropbox / Google Drive)." >&2

# Keep the newest ${KEEP} archives, drop the rest. while-read (not mapfile)
# for macOS bash 3.2 compatibility.
ls -1t "${BACKUP_DIR}"/claudeclaw-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while IFS= read -r f; do
  [[ -z "${f}" ]] && continue
  rm -f "${f}"
  echo "backup: pruned $(basename "${f}")"
done
