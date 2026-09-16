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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Overridable so a test can build a throwaway archive without touching the
# real backup directory (and its retention sweep).
BACKUP_DIR="${BACKUP_DIR:-${REPO_ROOT}/backups}"
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
BUNDLE=""
trap 'rm -f "${REPOLIST}" "${HOMELIST}" "${MANIFEST}"; [[ -n "${BUNDLE}" ]] && rm -f "${BUNDLE}"; rm -rf "${STAGE}"' EXIT

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
STORE_SKIP=" whisper health cowork venv-garmin venv-pdf dhl-chrome-profile fedex-labels fedex-vam archery-basis "
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
#   inbox/, progress/        -- channel runtime: raw attachments and seen-markers
#   *.pid                    -- channel runtime too: four 7-byte process ids that
#                               are wrong the moment the bot restarts. Named here
#                               because backup-coverage.py otherwise reports
#                               .claude/channels as a permanent gap, and a checker
#                               that is always red is a checker nobody reads.
#   __pycache__, node_modules -- build output
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
  find agents -type f \
    -not -path '*/.claude-config/*' \
    -not -path '*/inbox/*' \
    -not -path '*/progress/*' \
    -not -path '*/__pycache__/*' \
    -not -path '*/node_modules/*' \
    -not -path '*/.venv/*' \
    -not -name '*.pid' \
    -print >> "${REPOLIST}"
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
# MAIN orchestrator channel tokens + pairing state, per provider. bot.pid and
# inbox/ are runtime/transient and intentionally excluded. Since #915 the
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

# --- Manifest (stored at the archive root for self-description). -----------
{
  echo "Marveen backup ${STAMP}"
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
trap 'rm -f "${REPOLIST}" "${HOMELIST}" "${MANIFEST}" "${ARCHIVE_LIST}"; [[ -n "${BUNDLE}" ]] && rm -f "${BUNDLE}"; rm -rf "${STAGE}"' EXIT
tar -tzf "${ARCHIVE}" > "${ARCHIVE_LIST}"

missing=0
while IFS= read -r want; do
  # Manifest body lines only: the header block and the group separators are
  # prose, not paths.
  case "${want}" in repo/*|home/*) ;; *) continue ;; esac
  # A directory entry is listed once in the manifest and expands to many paths
  # in the archive, so match on the prefix, and anchor it so "store/x" cannot
  # be satisfied by "store/xyz".
  # verify-match-begin
  # The manifest path is data, not a pattern: a "$" in a class-file name
  # (MainActivity$2.class) is a line anchor to grep -E and never matches, so the
  # 2026-09-10 20:01 run reported three present files as MISSING and exited 6
  # over a good archive. Escape every ERE metacharacter before anchoring.
  want_re="$(printf '%s' "${want}" | sed -e 's/[][\.*^$+?(){}|]/\\&/g')"
  if ! grep -qE "^${want_re}(/|$)" "${ARCHIVE_LIST}"; then
  # verify-match-end
    echo "backup: MISSING from the archive: ${want}" >&2
    missing=$((missing + 1))
  fi
done < <(sed -e 's/  *(.*)$//' "${MANIFEST}")

# Load-bearing by name. Each one has already been lost or nearly lost once:
# the memory directories and the local-commit bundle on 2026-09-04, the
# credential-carrying store/ files by the whitelist that preceded it.
for marker in "repo/store/claudeclaw.db" "home/.claude/skills" "home/.claude/scheduled-tasks"; do
  grep -qE "^${marker}(/|$)" "${ARCHIVE_LIST}" || {
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

if [[ "${missing}" -gt 0 ]]; then
  echo "backup: FAILED verification -- ${missing} item(s) named in the manifest are not in ${ARCHIVE}." >&2
  echo "backup: the archive is kept for inspection, but do NOT treat it as a good copy." >&2
  # launchd sends this script's output to logs/backup.log, which nobody opens.
  # A loud failure into an unread file is the same silence the verification
  # above exists to break, so the failure also goes onto the agent message
  # queue, where it survives the agent being asleep at 04:30 and gets read on
  # the next turn. Best-effort: a messaging problem must not change the exit
  # code or mask the real failure.
  if [[ -x "${REPO_ROOT}/scripts/agent-msg.sh" ]]; then
    # The recipient is the main agent as .env names it. "halpali" was the
    # pre-rename id: measured 2026-09-10, zero rows to it in agent_messages, so
    # every failure notice this block ever sent was dropped on the floor.
    main_agent="$(sed -n 's/^MAIN_AGENT_ID=//p' "${REPO_ROOT}/.env" 2>/dev/null | head -1 | tr -d '"')"
    main_agent="${main_agent:-marveen}"
    bash "${REPO_ROOT}/scripts/agent-msg.sh" "${main_agent}" "${main_agent}" \
      "[MENTES] A napi mentes ellenorzese ELBUKOTT ${STAMP}-kor: ${missing} tetel hianyzik az archivumbol (reszletek: logs/backup.log). Az archivum NEM tekintheto jo masolatnak." \
      >/dev/null 2>&1 || true
  fi
  exit 6
fi
echo "backup: verified $(grep -cE '^(repo|home)/' "${MANIFEST}") manifest entries against the archive"

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
