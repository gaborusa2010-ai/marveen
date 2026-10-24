#!/usr/bin/env bash
# Marveen backup.
#
# The archive has two top-level groups so a restore is unambiguous about
# where each file belongs (see docs/MIGRATION.md):
#
#   repo/   -> extract under the project root (this repo)
#     store/**                 (DB WAL-checkpointed first; minus models,
#                               virtualenvs, browser profile and logs)
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

# The archive carries live secrets (store/.claude-oauth-token, .dashboard-token,
# the vault master key next to vault.json, every channel .env). It must be
# born 0600 -- not chmod-ed afterwards, because a crash between tar and chmod
# would leave a world-readable copy (BACKUPTITOK915: measured 0644 on the
# owner host under the default umask 022). umask 077 covers the archive, the
# backups/ dir, the staging dir and every temp file this script creates.
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Literal membership test for the archive verification below (card a8a92d55).
# shellcheck source=lib/archive-list-has.sh
. "${REPO_ROOT}/scripts/lib/archive-list-has.sh"
# Overridable so a test can build a throwaway archive without touching the
# real backup directory (and its retention sweep).
BACKUP_DIR="${BACKUP_DIR:-${REPO_ROOT}/backups}"

# ONE BACKUP AT A TIME. Nothing stopped two runs overlapping until 2026-09-23,
# and that evening the image archive (which had the same gap) proved it: two
# builds 17 minutes apart, both alive, 6.4 GB of disk for zero uploads. This
# script is the more dangerous of the two to double-run, because both copies
# WAL-checkpoint the same SQLite database and prune the same backups/ dir.
#
# THE LOCK HANGS OFF BACKUP_DIR, NOT THE REPO ROOT, and that is deliberate: a
# test overrides BACKUP_DIR to build a throwaway archive, and it must get its
# own lock rather than queue behind (or block) the real nightly run.
#
# Non-blocking, exit 0, and it says so: a second caller queuing up behind a
# 3 GB build would just repeat it once the first finished, and reporting a
# failure for "the work is already being done" trains everyone to ignore the
# alarm. A silent skip would be worse still -- that is how a backup quietly
# stops running.
mkdir -p "${BACKUP_DIR}"
BACKUP_LOCKFILE="${BACKUP_LOCK_FILE:-${BACKUP_DIR}/.backup.lock}"
exec {BACKUP_LOCKFD}>"${BACKUP_LOCKFILE}"
if ! flock -n "${BACKUP_LOCKFD}"; then
  echo "mentes: MAR FUT egy masik mentes (${BACKUP_LOCKFILE}) -- kihagyom"
  exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"

# --- FULL or DIFFERENTIAL? ------------------------------------------------
# Until 2026-09-22 every run was a full copy. The owner asked for the change
# after seeing the size (Telegram msg 3533): "csak azt mentsuk el ami
# valtozott,ami hozza lett adva...". Measured that evening on the tree this
# script actually takes: 5452 files / 3371.7 MB whole, 204 files / 225.7 MB
# changed in 24 hours, 1023 files / 518.8 MB changed in 7 days.
#
# INCREMENTAL since 2026-09-26: every nightly archive is compared against the
# state the PREVIOUS verified archive recorded, so a file goes up once, not
# every night until the next full. From 09-22 to 09-26 it was differential
# (against the weekly full) and grew 188 -> 351 -> 706 MB in three nights; the
# owner confirmed the incremental form (Telegram msg 4139, "Csak a napi
# valtozas (ahogy kertem)"). Restore = the newest full, then every incremental
# after it, oldest first. The reasoning in full: scripts/backup-delta.py.
# (The archive suffix stays "-diff" so the retention below and the Drive
# pruner in scripts/muhely/drive_upload.py keep recognising the chain.)
#
# Forcing a full run: `BACKUP_MODE=full scripts/backup.sh` or `--full`.
# Overridable for the same reason BACKUP_DIR is: a test must be able to
# exercise the full/incremental pair without touching the real baseline.
BASELINE="${BASELINE:-${REPO_ROOT}/store/backup-baseline.json}"
FULL_EVERY_DAYS="${FULL_EVERY_DAYS:-7}"
BACKUP_MODE="${BACKUP_MODE:-auto}"
[[ "${1:-}" == "--full" ]] && BACKUP_MODE=full
if [[ "${BACKUP_MODE}" == "auto" ]]; then
  if python3 "${REPO_ROOT}/scripts/backup-delta.py" due \
       --baseline "${BASELINE}" --backup-dir "${BACKUP_DIR}" \
       --max-age-days "${FULL_EVERY_DAYS}"; then
    BACKUP_MODE=full
  else
    BACKUP_MODE=diff
  fi
fi
if [[ "${BACKUP_MODE}" == "full" ]]; then
  ARCHIVE="${BACKUP_DIR}/claudeclaw-${STAMP}-full.tar.gz"
else
  ARCHIVE="${BACKUP_DIR}/claudeclaw-${STAMP}-diff.tar.gz"
fi
echo "backup: mode=${BACKUP_MODE}  archive=$(basename "${ARCHIVE}")"

# Retention is chain-aware now: a full archive may not be pruned while a
# incremental still depends on it, so a flat "keep the newest 14" would break
# restores. KEEP_FULL full archives are kept, plus every incremental newer
# than the oldest kept full.
KEEP_FULL="${KEEP_FULL:-2}"

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
# The staging tree lives next to the archives, NOT in $TMPDIR. macOS runs
# com.apple.bsd.dirhelper daily at 03:35 (StartCalendarInterval) with
# CLEAN_FILES_OLDER_THAN_DAYS=3: it deletes $TMPDIR files whose atime is older
# than three days. `cp -p` below preserves the SOURCE atime, so every file that
# is rarely read (an agent's CLAUDE.md, a channel .env, the dashboard token, a
# small side database) arrives in the stage already "three days old" and can be
# swept between the copy and the tar. The archive is still written, the
# verification below then reports the holes, and the nightly job fails.
# BACKUP_DIR is not a sweep target, and the EXIT trap still removes the stage.
STAGE="$(mktemp -d "${BACKUP_DIR}/.stage.XXXXXX")"
BUNDLE=""
# Declared up front so every trap below can name them unconditionally: the
# incremental filter (further down) only fills them in when it runs.
REPOLIST_FULL="${REPOLIST}"
HOMELIST_FULL="${HOMELIST}"
REPOLIST_DIFF=""
HOMELIST_DIFF=""
PENDING_STATE=""
cleanup() {
  rm -f "${REPOLIST}" "${HOMELIST}" "${REPOLIST_FULL}" "${HOMELIST_FULL}" "${MANIFEST}" ${ARCHIVE_LIST:+"${ARCHIVE_LIST}"}
  [[ -n "${REPOLIST_DIFF}" ]] && rm -f "${REPOLIST_DIFF}"
  [[ -n "${HOMELIST_DIFF}" ]] && rm -f "${HOMELIST_DIFF}"
  [[ -n "${PENDING_STATE}" ]] && rm -f "${PENDING_STATE}"
  [[ -n "${BUNDLE}" ]] && rm -f "${BUNDLE}"
  rm -rf "${STAGE}"
  return 0
}
trap cleanup EXIT

# add_if <listfile> <base> <relpath>  -- append relpath when <base>/<relpath> exists.
add_if() {
  local list="$1" base="$2" rel="$3"
  if [[ -e "${base}/${rel}" ]]; then echo "${rel}" >> "${list}"; fi
}

# repo/ group (relative to REPO_ROOT)
# store/ -- everything EXCEPT the bulky, regenerable and transient parts.
# Until 2026-09-04 this was a five-name whitelist (the DB, its -shm/-wal, the
# dashboard token, config-overrides.json) and every OTHER file in store/ sat
# outside the archive without ever saying so: the credential vault, the
# verified-recipients ledger that gates every outgoing letter, the egress
# allowlist, the autonomy levels, the per-service API tokens, and hand-made
# data that exists nowhere else (the mail-partner categorisation, the SEO
# baselines, the webshop change log). A whitelist is silent about every file
# added after it was written, so the rule is inverted here: take store/ and
# name only what must stay OUT.
#   whisper, health, cowork, venv-*, dhl-chrome-profile, fedex-labels,
#   fedex-vam, archery-basis  -- models, exports, virtualenvs, a browser
#     profile and generated PDFs: 3.2 GB, all re-downloadable or reproducible
#   *.log, *.out, *.pid       -- runtime noise, worthless in a restore
# What remains is ~4 MB next to the DB, so the archive stays small.
#   backups                   -- store/backups holds OTHER machines' tarballs (two 2026-07-13
#     hermes dumps, 701 MB): a backup inside the backup, and not this host's state
#   darwin-relay              -- relay.log (87 MB): a log, not state; nothing restores from it
#   scheduled-runs            -- SCHEDPROMPTREF917 fire-time snapshots, ~100 files/day,
#     7-day retention on disk already (scheduled-run-snapshot.ts); regenerated on every
#     large-task fire, so a restore losing yesterday's costs nothing
#   (measured 2026-09-16: these two were 788 MB of a 948 MB archive; excluding them
#    leaves ~150 MB. STORE_SKIP does not delete anything -- the files stay on disk.)
STORE_SKIP=" whisper health cowork venv-garmin venv-pdf dhl-chrome-profile fedex-labels fedex-vam archery-basis backups darwin-relay scheduled-runs "
if [[ -d store ]]; then
  while IFS= read -r _entry; do
    _name="$(basename "${_entry}")"
    case "${STORE_SKIP}" in *" ${_name} "*) continue ;; esac
    case "${_name}" in *.log|*.out|*.pid) continue ;; esac
    echo "store/${_name}" >> "${REPOLIST}"
  done < <(find store -mindepth 1 -maxdepth 1)
fi
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
# A CLAUDE-APPBOL ATHOZOTT PROJEKTEK NYERSANYAGA. A gazda 2026-09-06-i dontese szerint
# hetente egy projekt jon at, csak hetvegen -- a forrasuk a Claude appban van, ahonnan
# nekunk NINCS hozzaferesunk, tehat ha ez a masolat elvesz, csak O tudja ujra eloallitani.
# Az elso: creating-ai-agents (6 fajl, 112 kB), 2026-09-06.
add_if "${REPOLIST}" "${REPO_ROOT}" store/projekt-athozatal
# A YOUTUBE-MUNKA NYERSANYAGA ES LELETE (Anorak ejszakai muszakjai).
# Felvetel oka, merve 2026-09-08 08:1x a backup-coverage.py-vel: harom mappa TELJESEN
# fedetlen volt -- sem mentesben, sem tiszta git-allapotban egyetlen fajl sem.
#   store/yt-nyers-sandeep  144 fajl  17 MB   (2026-09-06 22:12)
#   store/yt-nyers-0723      62 fajl  12 MB   (2026-09-06 21:34)
#   store/yt-lelet            3 fajl 276 kB   (2026-09-07 23:16)
# ES A LENYEG: az elso ketto MAR LETEZETT a 2026-09-07 20:00-as mentes idejen, megis
# kimaradt. Nem idozites-kerdes volt, hanem az, hogy a `store/` blokk NEVSZERINTI
# engedelylista -- pontosan az a hibaosztaly, amit ez a fajl az `agents/`-nel maga ir le
# nehany sorral lentebb: "az allowlist minden UJ mappat lathatatlanna tesz
# alapertelmezesben, es semmi nem jelzi".
# MIERT ERDEMES MENTENI: a nyersanyag ELVILEG ujraletolthető, de a fetch-budget szerint
# a darabszam a kotoelem, nem a sebesseg (a learn.kregtool.com 140 oldal utan KIZARTA a
# flottat, es egy kizart forras nem visszavonhato). A `yt-lelet` ennel is egyszerubb eset:
# az mar a feldolgozas EREDMENYE, nem a nyersanyag -- 276 kB, es sehol maskepp nem all elo.
# ES A JAVITAS ALAKJA, mert az elso valtozat MAR KEVES VOLT -- ket perc alatt kiderult:
# harom mappat nevesitettem, es a kovetkezo lefedettseg-meres AZONNAL ket ujat mutatott
# (yt-nyers-joshua 66 fajl, yt-nyers-mindennapok 11 fajl, mindketto 2026-09-07 23:1x).
# A store-ban ma HET `yt-*` mappa all. Ha egyenkent sorolom fel oket, ugyanabba a
# csapdaba estem volna, amit a fenti bekezdes leir: a nyolcadik mappa megint kimaradna.
# Ezert MINTA, nem nevsor. Ugyanaz az ervelés, amivel az `agents/` blokk inverz lett.
find store -maxdepth 1 -type d -name 'yt-*' -print >> "${REPOLIST}" 2>/dev/null || true
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
#   progress/                -- channel runtime: seen-markers and the progress
#                               debug log. Still excluded; that one really is
#                               transient.
#   (inbox/ WAS excluded here until 2026-09-16.) Forgacs measured the assumption
#                               under it and it did not hold: the exclusion was
#                               argued as "the raw attachment is disposable
#                               because whatever matters is copied out by
#                               `jobs.py photo`". Of HIS 28 inbox photos exactly
#                               2 exist in store/, and both are hand-named, so
#                               the photo command did not put them there -- 26
#                               live only in the inbox. Measured across the fleet
#                               the same morning: 47 files, 5,1 MB in the
#                               sub-agent inboxes (cody 16, forgacs 28, margo 3,
#                               all JPG), and the work-log records carry ZERO
#                               photos in all 20 rows. The Telegram Bot API
#                               serves no history, so a lost attachment is gone
#                               for good. 5,1 MB against a 2,99 GB archive is
#                               0,17 percent.
#   *.pid                    -- channel runtime too: four 7-byte process ids that
#                               are wrong the moment the bot restarts. Named here
#                               because backup-coverage.py otherwise reports
#                               .claude/channels as a permanent gap, and a checker
#                               that is always red is a checker nobody reads.
#   __pycache__, node_modules -- build output
#   target/{debug,release,tmp} -- Rust (cargo) build output. Measured 2026-10-24
#                               20:2x: agents/cody/external/solidcraft/target was
#                               23 GB and turned the weekly full archive from
#                               1.3 GB (10-16) into 9.9 GB. Rebuilt by `cargo
#                               build` from the sources next to it, which stay in.
#   .venv                    -- egy agens sajat Python-kornyezete. Margo
#                               agents/margo/.venv-je MERVE 2026-09-08 20:3x:
#                               826 MB es 15 080 fajl, vagyis az agents/ alol
#                               vitt 17 378 fajl 87 SZAZALEKA. Kizarhato, mert
#                               UJRAEPITHETO: a mellette allo requirements.txt
#                               (32 sor) BENNE MARAD a mentesben, es abbol a
#                               kornyezet egy paranccsal visszaall. A Drive-on
#                               8 peldanyt tartunk (drive_upload.py --keep
#                               alapertelmezese; SENKI nem ad neki mast, ez
#                               megmerve), tehat kb. 6,6 GB, ami semmivel nem
#                               tobb tudas. Margo jelezte.
#   (*.bak-* MAR NEM)         -- MEGSZUNT 2026-09-08 21:2x. Az indoka az volt, hogy egy
#                               .bak eldobhato masolat egy fajlrol, amit amugy is
#                               viszunk. A store/ alatt ez all. ITT NEM: az egesz
#                               agents/ fa GITIGNORE-olt (.gitignore 19. sor, es a
#                               `git ls-files agents/` NULLA fajlt ad), tehat a hat
#                               agens CLAUDE.md-jenek es SOUL.md-jenek NINCS tortenete
#                               sehol -- a .bak lett az EGYETLEN tortenet-hordozo,
#                               es epp azt zartuk ki. Margo vette eszre, ket kulon
#                               meresembol, amiket en nem tettem ossze.
#                               AZ ARA MERVE ugyanakkor, es a KETTo NEM UGYANAZ:
#                               32 ilyen fajl all az agents/ fan, de csak 27 kerul
#                               be (382 561 bajt = 0,36 MB) -- a masik ot a
#                               .claude-config/ alatt van, amit sajat, ervenyes
#                               indokkal zarunk ki. A tar mar amugy is 1,4 GB.
#                               Ha a `agents/*/CLAUDE.md` valaha gitbe kerul (kartya
#                               94883820), ez a kizaras VISSZATEHETO.
if [[ -d agents ]]; then
  # EGY NAPRA SZOLO, MAGATOL LEJARO KIZARAS -- store/backup-skip-once.txt
  #
  # Az elso sor egy DATUM (YYYY-MM-DD), a tobbi sor egy-egy `find -path` minta.
  # A kizaras CSAK akkor hat, ha az elso sor a MAI nap. Holnap a fajl magatol
  # hatastalan lesz, tehat egy elfelejtett bejegyzes nem tunteti el csendben
  # ugyanazt a mappat het napon at. Ures vagy hianyzo fajl = a mai viselkedes,
  # beture valtozatlanul.
  #
  # Miert kellett (2026-09-16 19:4x): harom napja nem volt ELLENORZOTT, gepen
  # kivuli masolat. A helyi archivum es a gpg RENDBEN elkeszult (2,99 GB), a
  # FELTOLTES allt meg 1152/2848 MB-nal. A gazda engedelye erre az egy estere:
  # Telegram 2899, "mehet", valaszul a 2898-ra, amiben pontosan ezt kertem.
  # EZ NEM VONJA VISSZA a 2026-09-05-i dontesét ("Mentes.", a teljes valtozat):
  # az marad az alapertelmezes, ez egyetlen futasra szol.
  _SKIP_FILE="${REPO_ROOT}/store/backup-skip-once.txt"
  _skip_args=()
  if [[ -f "${_SKIP_FILE}" ]]; then
    _skip_day="$(head -1 "${_SKIP_FILE}" | tr -d '\r\n ')"
    if [[ "${_skip_day}" == "$(date +%Y-%m-%d)" ]]; then
      while IFS= read -r _pat; do
        _pat="$(printf '%s' "${_pat}" | tr -d '\r')"
        [[ -z "${_pat}" || "${_pat}" == \#* ]] && continue
        _skip_args+=( -not -path "${_pat}" )
        echo "backup: EGY-NAPOS KIZARAS aktiv: ${_pat}" >&2
      done < <(tail -n +2 "${_SKIP_FILE}")
    else
      echo "backup: store/backup-skip-once.txt van, de a datuma (${_skip_day}) nem a mai -- FIGYELMEN KIVUL HAGYVA." >&2
    fi
  fi
  find agents -type f \
    -not -path '*/.claude-config/*' \
    -not -path '*/progress/*' \
    -not -path '*/__pycache__/*' \
    -not -path '*/node_modules/*' \
    -not -path '*/.venv/*' \
    -not -path '*/target/debug/*' \
    -not -path '*/target/release/*' \
    -not -path '*/target/tmp/*' \
    -not -name '*.pid' \
    "${_skip_args[@]}" \
    -print >> "${REPOLIST}"

  # THE PICTURES AND PDFs LIVE IN THEIR OWN ARCHIVE SINCE 2026-09-22.
  # The owner's decision (Telegram msg 3536/3539). Measured that evening: 1476
  # files, 2973.7 MB -- 88% of everything this script carried, and the part
  # that changes least. scripts/backup-images.sh takes them once and rebuilds
  # only when they move.
  #
  # `covered` is the load-bearing word. It does NOT print every image; it
  # prints only the ones whose current mtime+size match a version already
  # inside an UPLOADED image archive. A picture rendered an hour ago is not on
  # that list, so it stays in tonight's archive until the image backup catches
  # up. The failure this shape rules out is the one that matters: a file in
  # NEITHER archive. A file in both is merely wasteful.
  #
  # If backup-images.sh has never run, `covered` prints nothing and this block
  # subtracts nothing -- the nightly archive keeps carrying all of it, exactly
  # as before. Nothing is removed from cover by installing this; cover is
  # removed only by an image archive that provably holds the file.
  if [[ -x "${REPO_ROOT}/scripts/backup-images.sh" ]]; then
    _IMG_COVERED="$(mktemp -t claudeclaw-imgcov.XXXXXX)"
    if bash "${REPO_ROOT}/scripts/backup-images.sh" covered > "${_IMG_COVERED}" 2>/dev/null \
       && [[ -s "${_IMG_COVERED}" ]]; then
      _before="$(wc -l < "${REPOLIST}" | tr -d ' ')"
      _KEPT="$(mktemp -t claudeclaw-kept.XXXXXX)"
      # -F -x -f: whole-line, fixed-string matching, so a path is never treated
      # as a pattern and never matches a longer path that merely contains it.
      grep -F -x -v -f "${_IMG_COVERED}" "${REPOLIST}" > "${_KEPT}" || true
      mv "${_KEPT}" "${REPOLIST}"
      _after="$(wc -l < "${REPOLIST}" | tr -d ' ')"
      echo "backup: $((_before - _after)) kep/PDF kihagyva (a keparchivum mar tartalmazza oket)"
    fi
    rm -f "${_IMG_COVERED}"
  fi
fi

# The PROJECT's own Claude configuration: settings.json (which wires up the
# hooks), its skills, and its sub-agent definitions. The user-level twin
# (~/.claude/settings.json) has a backup line below and this one had none --
# the same "two settings.json files, one of them forgotten" shape the fleet has
# already been bitten by once. 13 files, 180 kB, in no backup and in no git.
#
# A `*.bak-*` KIZARASA ITT IS MEGSZUNT, 2026-09-08 21:3x, ugyanazzal az ervvel, mint
# az agents/ blokknal -- es Margo talalta meg ezt a MASODIK peldanyt, miutan az elsot
# bezartuk. MERVE: a .claude fa 20 fajlbol EGYET tart a gitben (a settings.json-t, egy
# gitignore-negacio miatt), tehat a benne allo 14 projekt-skillnek NINCS tortenete
# sehol -- a .bak az egyetlen. AZ ARA: 2 fajl, 10 681 bajt.
# A gyoker-blokkban (lentebb) a kizaras HELYES MARAD: az altala fedett mappak
# (scripts 181, src 581, docs 51, templates 15 kovetett fajl) mind gitben vannak,
# ott a .bak valoban eldobhato masolat.
if [[ -d .claude ]]; then
  find .claude -type f -print >> "${REPOLIST}"
fi

# EVERYTHING ELSE IN THE REPO ROOT, inverted the same way agents/ is above.
#
# The block above agents/ already argues this: an allowlist makes every NEW
# directory invisible by default. That argument was applied one level down and
# not at the root, so the root stayed a list of names -- and the names that were
# never on it were the product's own source. Measured 2026-09-06 15:25 with
# scripts/backup-coverage.py, against the 09:46 archive:
#   src   -- 38 files in NO copy at all (16 of them brand-new test files that
#            have never been committed either), 594 files total
#   docs  -- 3 files in no copy, including docs/marveen-agent-reference.md,
#            the command reference every agent in the fleet reads
#   seed-skills -- 3 files in no copy, two of them whole skills written this week
# Roughly 800 kB of work that existed on exactly one disk. The daily sentinel
# (mentes-lemaradas-ellenorzes, 08:15) has been printing these same three lines
# every morning; its own instructions say an unchanged list is not news, so the
# report was correct and inert at the same time.
#
# WHY IT IS A SWEEP AND NOT FOUR MORE add_if LINES: 2026-09-03 the fix for
# scripts/ was "take the whole tree, not another named subdirectory, because the
# next new directory would be missed again". Naming src, docs and seed-skills
# here would repeat exactly the mistake that comment was written about.
#
# Exclusions are decisions, so each one carries its reason:
#   node_modules, dist, dist.bak-*  -- build output and copies of it
#   backups                          -- the archives themselves
#   coverage, logs, .venv, venv      -- test output and runtime noise
#                                       (same set as backup-coverage.py SKIP_REPO)
#   .git, store, agents, .claude,    -- ALREADY TAKEN ABOVE, whole or selectively.
#   scripts, templates, CLAUDE.md,      Repeating them here would re-copy ~590 MB.
#   SOUL.md, .env, scheduled-tasks.json
# Cost MEASURED the day it was written, by running this very block against the
# live repo: 768 files, 24.2 MB, on a 575 MB archive -- plus 4.2%. The two big
# shares are web/ (17.3 MB, mostly the fleet avatars) and src/ (4.8 MB).
while IFS= read -r rootentry; do
  case "${rootentry}" in
    node_modules|dist|backups|coverage|.venv|venv|logs) continue ;;
    dist.bak-*) continue ;;
    .git|store|agents|.claude|scripts|templates) continue ;;
    CLAUDE.md|SOUL.md|.env|scheduled-tasks.json) continue ;;
  esac
  if [[ -d "${rootentry}" ]]; then
    find "${rootentry}" -type f \
      -not -path '*/__pycache__/*' \
      -not -path '*/node_modules/*' \
      -not -name '*.bak-*' \
      -not -name '*.pid' \
      -print >> "${REPOLIST}"
  else
    printf '%s\n' "${rootentry}" >> "${REPOLIST}"
  fi
done < <(find . -maxdepth 1 -mindepth 1 -printf '%f\n' 2>/dev/null | sort)

# home/ group (relative to $HOME)
add_if "${HOMELIST}" "${HOME}" .claude/skills
add_if "${HOMELIST}" "${HOME}" .claude/scheduled-tasks
# Retired one-shot tasks, moved out of scheduled-tasks/ once they fired (dir
# appeared 2026-09-27). Small (~124K), and the only record of what each
# one-shot promised and when; backup-coverage flagged it as fully uncovered.
add_if "${HOMELIST}" "${HOME}" .claude/scheduled-tasks-archive
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
# The file-based auto-memory. Until 2026-09-04 this was NOT in the archive, and
# a restore test that day proved what that costs: 490 markdown memories for the
# main agent alone, none of them in the tarball. The SQLite copy is not a
# substitute -- it holds the prose, not the frontmatter, the type or the
# [[links]] between memories. Only the memory/ directories are taken, not the
# whole projects/ tree, which is full of transcripts and tool-result dumps.
if [[ -d "${HOME}/.claude/projects" ]]; then
  ( cd "${HOME}" && find .claude/projects -maxdepth 2 -type d -name memory -print ) >> "${HOMELIST}"
fi
# MAIN orchestrator channel tokens + pairing state, per provider. bot.pid is
# runtime/transient and intentionally excluded. NOT inbox/ any more: see the
# 2026-09-16 note in the header list. On a migrated install the main inbox is
# already carried by the repo-root .claude block further up; this HOME branch
# only matters for an unmigrated one. Since #915 the
# main state dir is install-scoped (<repo>/.claude/channels/<provider>); the
# HOME base only still holds it on an unmigrated install -- take both, each
# from its own list so restore puts them back where they came from.
if [[ -d "${HOME}/.claude/channels" ]]; then
  ( cd "${HOME}" && find .claude/channels -maxdepth 2 \
      \( -name '.env' -o -name 'access.json' -o -name 'invites.json' \) \
      -print ) >> "${HOMELIST}"
  ( cd "${HOME}" && find .claude/channels -maxdepth 2 -type d -name 'approved' -print ) >> "${HOMELIST}"
fi
if [[ -d "${REPO_ROOT}/.claude/channels" ]]; then
  ( cd "${REPO_ROOT}" && find .claude/channels -maxdepth 2 \
      \( -name '.env' -o -name 'access.json' -o -name 'invites.json' \) \
      -print ) >> "${REPOLIST}"
  ( cd "${REPO_ROOT}" && find .claude/channels -maxdepth 2 -type d -name 'approved' -print ) >> "${REPOLIST}"
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

# The sweep over the repo root can name a path one of the add_if lines above
# already took. Duplicates are harmless to the copy (it just runs twice) but they
# would show up twice in the manifest, and the manifest is what backup-coverage.py
# reads -- a checker should not have to guess whether two identical lines mean two
# files. First occurrence wins, so the grouped, commented order above is kept.
if [[ -s "${REPOLIST}" ]]; then
  awk '!seen[$0]++' "${REPOLIST}" > "${REPOLIST}.dedup" && mv "${REPOLIST}.dedup" "${REPOLIST}"
fi

# --- Local commits that live on no remote. ---------------------------------
# This archive deliberately carries unversioned state, not the source: the
# source is supposed to live on a git remote. On 2026-09-04 that assumption
# broke -- nine days of work sat committed locally and pushed nowhere, so the
# only copy was this disk, and the tarball did not hold it either. A bundle of
# every local branch that origin does not already have closes the gap for a few
# hundred KB (the full history is 26 MB, but the shared part is recoverable by
# cloning origin). Restore, after cloning origin:
#   git fetch <restored>/repo/local-commits.bundle 'refs/heads/*:refs/heads/*'
if command -v git >/dev/null 2>&1 && [[ -d "${REPO_ROOT}/.git" ]]; then
  BUNDLE="$(mktemp -t claudeclaw-bundle.XXXXXX)"
  # An empty ref set makes `git bundle` refuse with "empty bundle", which is
  # the GOOD case (everything is already pushed), not an error -- so a failure
  # here just drops the file instead of failing the backup.
  if git -C "${REPO_ROOT}" bundle create "${BUNDLE}" \
       --branches --not --remotes=origin >/dev/null 2>&1; then
    echo "backup: local-commits.bundle $(wc -c < "${BUNDLE}" | awk '{print $1}') bytes"
  else
    rm -f "${BUNDLE}"; BUNDLE=""
    echo "backup: no local-only commits to bundle"
  fi
fi

if [[ ! -s "${REPOLIST}" && ! -s "${HOMELIST}" ]]; then
  echo "backup: nothing to archive" >&2
  exit 0
fi

# --- Coverage manifest: ALWAYS the full picture. ---------------------------
# scripts/backup-coverage.py answers "what does the backup set NOT cover?" from
# store/.last-backup-manifest.txt. That question is about the whole protected
# set, not about tonight's delta, so this copy is written from the UNFILTERED
# lists -- before the incremental filter runs below. Writing an incremental's
# short list here would make every unchanged file look uncovered.
{
  echo "Marveen backup coverage ${STAMP} (mode=${BACKUP_MODE}; this list is the FULL protected set, not the archive contents)"
  echo "--- repo/ ---"; sed 's,^,repo/,' "${REPOLIST}" 2>/dev/null || true
  echo "--- home/ ---"; sed 's,^,home/,' "${HOMELIST}" 2>/dev/null || true
} > "${REPO_ROOT}/store/.last-backup-manifest.txt" 2>/dev/null || true

# REPOLIST_FULL / HOMELIST_FULL were pinned to the unfiltered lists at the top:
# the baseline snapshot after a successful FULL run must describe the whole set,
# not whatever subset got archived.

# --- Incremental filter. --------------------------------------------------
# Drops every file byte-identical (mtime + size) to the state the previous
# verified archive recorded (full or incremental).
# Directories in the lists are expanded to files first, because that is what
# `cp -pR` would actually stage.
if [[ "${BACKUP_MODE}" == "diff" ]]; then
  REPOLIST_DIFF="$(mktemp -t claudeclaw-repo-diff.XXXXXX)"
  HOMELIST_DIFF="$(mktemp -t claudeclaw-home-diff.XXXXXX)"
  PENDING_STATE="$(mktemp -t claudeclaw-pending.XXXXXX)"
  if python3 "${REPO_ROOT}/scripts/backup-delta.py" filter \
       --baseline "${BASELINE}" --repo-root "${REPO_ROOT}" --home "${HOME}" \
       --repolist "${REPOLIST_FULL}" --homelist "${HOMELIST_FULL}" \
       --out-repo "${REPOLIST_DIFF}" --out-home "${HOMELIST_DIFF}" \
       --pending "${PENDING_STATE}"; then
    REPOLIST="${REPOLIST_DIFF}"
    HOMELIST="${HOMELIST_DIFF}"
  else
    # The filter refuses rather than guesses when the baseline is unusable.
    # Falling through to a full archive is the safe direction: a too-large
    # backup costs bandwidth, a too-small one costs data.
    echo "backup: the incremental filter failed -- falling back to a FULL archive" >&2
    BACKUP_MODE=full
    ARCHIVE="${BACKUP_DIR}/claudeclaw-${STAMP}-full.tar.gz"
  fi
fi

# --- Manifest (stored at the archive root for self-description). -----------
{
  echo "Marveen backup ${STAMP}"
  echo "mode: ${BACKUP_MODE}$( [[ "${BACKUP_MODE}" == "diff" ]] && echo " (incremental)   base: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["archive"])' "${BASELINE}" 2>/dev/null || echo '?')" )"
  if [[ "${BACKUP_MODE}" == "diff" ]]; then
    echo "previous links: $(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1])).get("chain") or []) or "(none)")' "${BASELINE}" 2>/dev/null || echo '?')"
    echo "RESTORE NEEDS THE WHOLE CHAIN: the base named above first, then every link listed, oldest first, then this one last."
  fi
  echo "host: $(hostname 2>/dev/null || echo '?')   user: ${USER:-?}   home: ${HOME}"
  echo "repo root: ${REPO_ROOT}"
  echo "Restore: tar -xpzf <archive> -C <tmp>; copy repo/* -> project root, home/* -> \$HOME."
  echo "See docs/MIGRATION.md for the full runbook (TCC, launchd paths, one-bot-one-poller, venv rebuild)."
  echo "--- repo/ ---"; sed 's,^,repo/,' "${REPOLIST}" 2>/dev/null || true
  if [[ -n "${BUNDLE}" ]]; then
    echo "repo/local-commits.bundle   (git bundle: local branches absent from origin)"
  fi
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
# (Written ABOVE, from the unfiltered lists -- see the coverage-manifest block.)

STAGE_FAILS=0
stage_group() {  # stage_group <listfile> <base> <group>
  local list="$1" base="$2" group="$3" rel parent
  [[ -s "${list}" ]] || return 0
  while IFS= read -r rel; do
    [[ -z "${rel}" ]] && continue
    parent="$(dirname "${rel}")"
    mkdir -p "${STAGE}/${group}/${parent}"
    # BACKUPALERT925: one unreadable file (e.g. a root-owned 0600 leftover) used
    # to abort the whole run under `set -e`, so no archive was written at all.
    # Skip it loudly instead; the manifest still names it, so the verification
    # below reports it as MISSING and the run fails with exit 6 and an alert.
    if ! cp -pR "${base}/${rel}" "${STAGE}/${group}/${parent}/"; then
      # A directory entry can be PARTIALLY copied (one unreadable file inside):
      # the manifest names the directory, which did get in, so verification
      # alone would pass. Count every staging failure and fail the run on it.
      STAGE_FAILS=$((STAGE_FAILS + 1))
      echo "backup: WARN could not fully stage ${group}/${rel} -- the run will fail (exit 6)" >&2
    fi
  done < "${list}"
}

stage_group "${REPOLIST}" "${REPO_ROOT}" repo
stage_group "${HOMELIST}" "${HOME}" home

if [[ -n "${BUNDLE}" ]]; then
  mkdir -p "${STAGE}/repo"
  cp -p "${BUNDLE}" "${STAGE}/repo/local-commits.bundle"
  chmod 600 "${STAGE}/repo/local-commits.bundle"
fi

# Archive only the top-level entries that exist (a group dir is absent when
# its list was empty), so tar never errors on a missing entry and the names
# stay clean (no leading "./").
( cd "${STAGE}" && tar -czf "${ARCHIVE}" MANIFEST.txt \
    $( [[ -d repo ]] && echo repo ) $( [[ -d home ]] && echo home ) )
echo "backup: wrote ${ARCHIVE} ($(wc -c < "${ARCHIVE}" | awk '{print $1}') bytes)"

# --- Verify the archive against the manifest. ------------------------------
# The manifest says what the backup INTENDED to carry; until now nothing
# checked what it actually carries. That gap is exactly how 2026-09-04
# happened: the store/ whitelist had been silently dropping files for weeks
# and the restore test was what finally noticed, not the backup itself. A
# backup that cannot say what is inside it is a promise, not a copy.
#
# Two checks, because they fail differently:
#   - every manifest entry has a matching path in the archive (a staging copy
#     that silently did nothing shows up here),
#   - a few load-bearing items are present by name (an archive that is valid,
#     small and useless -- the 09-04 shape -- shows up here even if the
#     manifest itself was built wrong).
ARCHIVE_LIST="$(mktemp -t claudeclaw-verify.XXXXXX)"
trap cleanup EXIT   # ARCHIVE_LIST is picked up by the same cleanup()
tar -tzf "${ARCHIVE}" > "${ARCHIVE_LIST}"

missing=0
while IFS= read -r want; do
  # Manifest body lines only: the header block and the group separators are
  # prose, not paths.
  case "${want}" in repo/*|home/*) ;; *) continue ;; esac
  # A directory entry is listed once in the manifest and expands to many paths
  # in the archive, so match on the prefix, and anchor it so "store/x" cannot
  # be satisfied by "store/xyz". LITERALLY (card a8a92d55): as a regex, a route
  # dir like `[id]` never matched itself and failed a complete backup.
  # verify-match-begin
  # The manifest path is data, not a pattern: a "$" in a class-file name
  # (MainActivity$2.class) is a line anchor to grep -E and never matches, so the
  # 2026-09-10 20:01 run reported three present files as MISSING and exited 6
  # over a good archive. Since v1.42.0 the match is upstream's literal
  # archive_list_has (scripts/lib/archive-list-has.sh), which covers the same case.
  if ! archive_list_has "${ARCHIVE_LIST}" "${want}"; then
  # verify-match-end
    echo "backup: MISSING from the archive: ${want}" >&2
    missing=$((missing + 1))
  fi
done < <(sed -e 's/  *(.*)$//' "${MANIFEST}")

# Load-bearing by name. Each one has already been lost or nearly lost once:
# the memory directories and the local-commit bundle on 2026-09-04, the
# credential-carrying store/ files by the whitelist that preceded it.
#
# FULL RUNS ONLY, and the reason matters: an incremental legitimately omits
# everything that did not change, so demanding these names there would fail a
# correct archive on any quiet day. The guarantee they encode still holds,
# because an incremental is never restored alone -- its base is a full archive
# that passed exactly this check. Card: the 2026-09-21 truncated backup (#298).
if [[ "${BACKUP_MODE}" == "full" ]]; then
  for marker in "repo/store/claudeclaw.db" "home/.claude/skills" "home/.claude/scheduled-tasks"; do
    archive_list_has "${ARCHIVE_LIST}" "${marker}" || {
      echo "backup: MISSING load-bearing item: ${marker}" >&2
      missing=$((missing + 1))
    }
  done
  # The file-based memories: only required when this host actually has some, so
  # a fresh install does not fail its first backup.
  if ( cd "${HOME}" && find .claude/projects -maxdepth 2 -type d -name memory -print -quit 2>/dev/null | grep -q . ); then
    grep -qE "^home/\.claude/projects/.*/memory(/|$)" "${ARCHIVE_LIST}" || {
      echo "backup: MISSING load-bearing item: the file-based memory directories" >&2
      missing=$((missing + 1))
    }
  fi
fi

if [[ "${STAGE_FAILS}" -gt 0 ]]; then
  echo "backup: FAILED staging -- ${STAGE_FAILS} entr(y/ies) could not be copied completely (see WARN lines above)." >&2
  missing=$((missing + STAGE_FAILS))
fi

if [[ "${missing}" -gt 0 ]]; then
  echo "backup: FAILED verification -- ${missing} item(s) named in the manifest are not in ${ARCHIVE}." >&2
  echo "backup: the archive is kept for inspection, but do NOT treat it as a good copy." >&2
  # launchd sends this script's output to logs/backup.log, which nobody opens.
  # A loud failure into an unread file is the same silence the verification
  # above exists to break, so the failure also goes onto the agent message
  # queue, where it survives the agent being asleep at 04:30 and gets read on
  # the next turn. Best-effort: a messaging problem must not change the exit
  # code or mask the real failure.
  # To the install's own main agent: MAIN_AGENT_ID as resolved near the top of
  # this script (from .env the way the app reads it: last definition wins,
  # quotes stripped, default "marveen"), never a fixed name -- an id that does
  # not exist on this install makes the alert go nowhere (card a8a92d55). Do not
  # re-read .env here: a second, simpler parse took the FIRST definition, kept
  # the quotes, and without `|| true` aborted the script under pipefail on an
  # install with no MAIN_AGENT_ID line (card a95cada0).
  # -f, not -x: the call below is `bash agent-msg.sh`, which needs no exec bit,
  # and since 6f7e65d1 (2026-09-16) git stores it as 100644 -- the -x guard
  # skipped every failure alert from then on without a word (card #578).
  if [[ -f "${REPO_ROOT}/scripts/agent-msg.sh" ]]; then
    # BACKUPALERT925: the recipient used to be a literal agent name from the
    # author's machine, so on any other fleet the alert went nowhere and the
    # swallowed error hid that too. Address this install's main agent.
    # LOCAL: "halpali" was this install's pre-rename id -- measured 2026-09-10,
    # zero rows to it in agent_messages, so every failure notice this block had
    # sent until then was dropped on the floor. MAIN_AGENT_ID is resolved from
    # .env above (the launchd block), with the same "marveen" default.
    bash "${REPO_ROOT}/scripts/agent-msg.sh" "${MAIN_AGENT_ID}" "${MAIN_AGENT_ID}" \
      "[MENTES] A napi mentes ellenorzese ELBUKOTT ${STAMP}-kor: ${missing} tetel hianyzik az archivumbol (reszletek: logs/backup.log). Az archivum NEM tekintheto jo masolatnak." \
      >/dev/null 2>&1 || echo "backup: WARN could not queue the failure alert for ${MAIN_AGENT_ID}" >&2
  else
    echo "backup: WARN no scripts/agent-msg.sh -- the failure alert for ${MAIN_AGENT_ID} was not queued" >&2
  fi
  exit 6
fi
echo "backup: verified $(grep -cE '^(repo|home)/' "${MANIFEST}") manifest entries against the archive"

# The archive contains sensitive tokens (dashboard bearer, channel bot tokens,
# project .env secrets). Do not auto-sync ${BACKUP_DIR} to iCloud, Dropbox,
# Google Drive, or any other cloud-backup folder. Keep it local.
echo "backup: WARNING -- archive contains sensitive tokens; keep ${BACKUP_DIR} out of cloud-sync folders (iCloud / Dropbox / Google Drive)." >&2

# --- The baseline: only after a FULL archive has been verified. ------------
# Written from the UNFILTERED lists, so the next incremental compares against
# the whole protected set. The ordering is the load-bearing part: a baseline
# written before verification would quietly make every following incremental
# incomplete, and nothing downstream would notice -- the archives would all be
# valid, just missing the files the failed full run never carried.
if [[ "${BACKUP_MODE}" == "full" ]]; then
  python3 "${REPO_ROOT}/scripts/backup-delta.py" snapshot \
    --baseline "${BASELINE}" --repo-root "${REPO_ROOT}" --home "${HOME}" \
    --repolist "${REPOLIST_FULL}" --homelist "${HOMELIST_FULL}" \
    --stamp "${STAMP}" --archive "$(basename "${ARCHIVE}")" || {
      echo "backup: could not write the baseline -- the next run will be FULL" >&2
      rm -f "${BASELINE}"
    }
else
  # Incremental: the state the filter saw becomes the new baseline, again only
  # after verification. If this fails the old state stays, and tomorrow's
  # incremental simply carries tonight's changes a second time -- wasteful,
  # never lossy.
  python3 "${REPO_ROOT}/scripts/backup-delta.py" advance \
    --baseline "${BASELINE}" --pending "${PENDING_STATE}" \
    --archive "$(basename "${ARCHIVE}")" || \
      echo "backup: could not advance the baseline -- tomorrow repeats tonight's changes" >&2
fi

# --- Retention, chain-aware. -----------------------------------------------
# A flat "keep the newest N" would eventually delete a full archive that a
# newer incremental still needs, and the loss would only surface during a
# restore -- the worst possible moment to find out. So: keep the newest
# ${KEEP_FULL} FULL archives, keep every incremental newer than the oldest
# kept full, drop everything else. Legacy archives from before 2026-09-22 carry
# neither suffix; they are treated as full, because that is what they are.
python3 - "${BACKUP_DIR}" "${KEEP_FULL}" <<'PRUNE'
import os, sys
backup_dir, keep_full = sys.argv[1], int(sys.argv[2])
try:
    # An encrypted copy whose plain archive is already gone is still part of
    # the chain by name. Until 2026-09-28 only *.tar.gz names were listed, so
    # such an orphan .gpg never matched the prune and 15 of them (4.2 GB, 08-23
    # to 09-08) sat here indefinitely. Strip the suffix so both halves are
    # judged by the same timestamp rule.
    names = sorted({(n[:-4] if n.endswith(".gpg") else n)
                    for n in os.listdir(backup_dir)
                    if n.startswith("claudeclaw-")
                    and (n.endswith(".tar.gz") or n.endswith(".tar.gz.gpg"))})
except OSError:
    sys.exit(0)
# Sort by the timestamp in the name, not by mtime: an upload or a copy can
# touch mtime, and the name is what a human reads when restoring.
names.sort(key=lambda n: n.split("claudeclaw-", 1)[1][:15])
fulls = [n for n in names if not n.endswith("-diff.tar.gz")]
if not fulls:
    sys.exit(0)
keep_fulls = set(fulls[-keep_full:])
floor = min(keep_fulls, key=lambda n: n.split("claudeclaw-", 1)[1][:15])
floor_stamp = floor.split("claudeclaw-", 1)[1][:15]
for n in names:
    stamp = n.split("claudeclaw-", 1)[1][:15]
    keep = n in keep_fulls or (n.endswith("-diff.tar.gz") and stamp >= floor_stamp)
    if keep:
        continue
    for path in (os.path.join(backup_dir, n), os.path.join(backup_dir, n + ".gpg")):
        try:
            os.remove(path)
        except OSError:
            continue
        print("backup: pruned " + os.path.basename(path))
PRUNE
