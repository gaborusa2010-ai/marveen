#!/usr/bin/env bash
# The image archive: taken ONCE, refreshed only when the pictures change.
#
# The owner's decision, Telegram msg 3536, 2026-09-22 20:44: "akkor a kepeket
# mantsuk el egyszer egy kulon mappaba, es csak akkor nyuljunk hozza ha valami
# valtozott... csinalj egy mappat a G Driveon es a Marveen files mappaban is".
#
# WHY IT IS WORTH A SEPARATE SCRIPT. Measured 2026-09-22 20:45: 1437 image
# files, 2835.5 MB, which is 84% of the 3371.7 MB the nightly backup carries.
# They are also the least volatile thing in the tree -- a finished colouring
# book does not change again. Paying to upload them every night was the single
# biggest waste in the backup, and it is the one part that a differential alone
# would NOT fix, because the weekly FULL still has to carry all of it.
#
# THE PREDICATE LIVES HERE, AND ONLY HERE. `list` prints exactly the files this
# archive covers, and scripts/backup.sh subtracts that same list from the
# nightly archive. One definition, two consumers: a file cannot fall between
# them, and it cannot end up in both. That is the shape the store/ allowlist
# failure of 2026-09-04 taught us to avoid -- two hand-kept lists drift, a
# generated one cannot.
#
# WHAT "CHANGED" MEANS: the fingerprint is the sorted path + mtime + size of
# every matched file. Content is not hashed: reading 2.8 GB to decide whether
# to write 2.8 GB costs about as much as writing it. A touched-but-identical
# file therefore triggers one needless rebuild, which is the cheap direction.
#
#   scripts/backup-images.sh list        paths only, one per line, repo-relative
#   scripts/backup-images.sh status      what would happen, changes nothing
#   scripts/backup-images.sh run         rebuild + upload IF the fingerprint moved
#   scripts/backup-images.sh run --force rebuild + upload regardless
#   scripts/backup-images.sh covered    the files PROVABLY inside the last
#                                       uploaded archive -- this is what
#                                       backup.sh is allowed to skip
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Overridable so a test can exercise the covered/exclusion pair without
# touching the real state (the same reason BACKUP_DIR and BASELINE are).
STATE="${IMAGE_STATE_FILE:-${REPO_ROOT}/store/backup-images-state.json}"
# A SUBDIRECTORY, AND THAT IS NOT COSMETIC. Every other tool in the fleet globs
# backups/claudeclaw-*.tar.gz non-recursively: the lateness watchdog looks for
# archives with no .gpg twin, the weekly task picks `ls -t backups/*.tar.gz |
# head -1` to encrypt and upload, and backup.sh's own retention parses the
# timestamp out of the name. An image archive sitting beside them would have
# been caught by all three -- reported as a broken run, uploaded in place of
# the real backup, and mis-sorted by the pruner into the position of a full
# archive. Measured 2026-09-22 20:52, on the first run, before it finished.
# One directory level makes it invisible to every one of those globs.
OUT_DIR="${IMAGE_BACKUP_DIR:-${REPO_ROOT}/backups/kepek}"
DRIVE_FOLDER="${IMAGE_DRIVE_FOLDER:-Marveen Kepek}"
cd "${REPO_ROOT}"

# Extension-based, not directory-based, and deliberately so: Roxy makes a new
# work folder most weeks, and a directory allowlist would silently stop
# covering the newest one while still looking maintained.
#
# PDFs are in here on the owner's instruction (Telegram msg 3539, 2026-09-22
# 20:47: "A kepeket tedd bele es a kesz PDF file-okat..."). Measured the same
# minute: 39 PDFs, 138.2 MB. Every one of them is kept, trial versions
# included, because "finished" is not a property this script can read off a
# filename -- the directory names (kesz/, final/, proba/) carry it, and they
# survive into the mirror unchanged, so he can see the difference himself.
list_images() {
  find agents -type f \
    \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \
       -o -iname '*.webp' -o -iname '*.tif' -o -iname '*.tiff' \
       -o -iname '*.pdf' \) \
    -not -path '*/.venv/*' \
    -not -path '*/node_modules/*' \
    -not -path '*/.claude-config/*' \
    -print 2>/dev/null | LC_ALL=C sort
}

fingerprint() {  # prints: <count> <bytes> <sha256 of the path/mtime/size list>
  list_images | while IFS= read -r f; do
    stat -c '%n %Y %s' "$f" 2>/dev/null || true
  done > "${1}"
  local count bytes sum
  count="$(wc -l < "${1}" | tr -d ' ')"
  bytes="$(awk '{s+=$NF} END {print s+0}' "${1}")"
  sum="$(sha256sum "${1}" | cut -d' ' -f1)"
  echo "${count} ${bytes} ${sum}"
}

case "${1:-status}" in
  list)
    list_images
    ;;

  mirror)
    # The browsable copy on the Windows desktop. The owner asked for the
    # pictures and the PDFs themselves here, not an archive (msg 3536 + 3539),
    # so this is a plain file mirror, laid out exactly as the source is under
    # agents/ -- which is what keeps kesz/ and proba/ telling him apart.
    #
    # Copies only what differs by mtime or size, so re-running it costs a stat
    # per file and nothing else. It never deletes: a mirror that prunes is one
    # bad predicate away from being the thing that loses the pictures.
    # On the Mac mini the desktop is the local one (card #583).
    if [ "$(uname -s)" = Darwin ]; then DEFAULT_DEST="$HOME/Desktop/Marveen files/Kepek"
    else DEFAULT_DEST="/mnt/c/Users/gabor/Desktop/Marveen files/Kepek"; fi
    DEST="${IMAGE_MIRROR_DIR:-$DEFAULT_DEST}"
    list_images > "${REPO_ROOT}/store/.image-mirror-list.tmp"
    python3 - "${REPO_ROOT}" "${DEST}" "${REPO_ROOT}/store/.image-mirror-list.tmp" <<'PY'
import os, shutil, sys
root, dest, listfile = sys.argv[1:4]
copied = skipped = 0
copied_bytes = 0
for line in open(listfile, encoding="utf-8", errors="surrogateescape"):
    rel = line.rstrip("\n")
    if not rel:
        continue
    src = os.path.join(root, rel)
    # Drop the leading "agents/" so the tree reads roxy/work/... not agents/roxy/...
    out_rel = rel[len("agents/"):] if rel.startswith("agents/") else rel
    dst = os.path.join(dest, out_rel)
    try:
        s = os.stat(src)
    except OSError:
        continue
    try:
        d = os.stat(dst)
        if int(d.st_mtime) == int(s.st_mtime) and d.st_size == s.st_size:
            skipped += 1
            continue
    except OSError:
        pass
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    copied += 1
    copied_bytes += s.st_size
print("kepek: tukrozes kesz -- %d fajl masolva (%.1f MB), %d valtozatlan, cel: %s"
      % (copied, copied_bytes / 1048576.0, skipped, dest))
PY
    rm -f "${REPO_ROOT}/store/.image-mirror-list.tmp"
    ;;

  covered)
    # Print only the files whose CURRENT mtime+size match what the last
    # successfully uploaded archive recorded. A picture added or re-rendered
    # since then is absent from this list, so backup.sh keeps carrying it until
    # the image archive catches up. The safe direction is a file in BOTH
    # archives; the unsafe one is a file in NEITHER, and this is the line
    # between them.
    python3 - "${STATE}" <<'PY'
import json, os, sys
try:
    covered = json.load(open(sys.argv[1])).get("covered") or {}
except Exception:
    sys.exit(0)
for path, want in covered.items():
    try:
        st = os.lstat(path)
    except OSError:
        continue
    if [int(st.st_mtime), int(st.st_size)] == want:
        print(path)
PY
    ;;

  status|run)
    MODE="${1:-status}"
    FORCE="${2:-}"

    # ONE REBUILD AT A TIME, AND THIS IS NOT THEORETICAL. Measured 2026-09-23:
    # two runs started 17 minutes apart (20:21 and 20:38) and BOTH built the
    # same archive, byte-identical in size, neither finishing. 6.4 GB of disk
    # for zero uploads. The WSL layer was ruled out as the cause -- the boot
    # has been continuous since 09-22 17:16 (journalctl --list-boots) -- so
    # nothing killed the first run; there was simply nothing stopping a second.
    #
    # ONLY `run` TAKES THE LOCK. `status`, `list` and `covered` are read-only
    # and are what a human or another script calls to ASK; a lock that blocks
    # the question as well as the answer turns a diagnostic into a hang.
    #
    # NON-BLOCKING, AND EXIT 0 ON CONTENTION. A second caller must not queue up
    # behind a 3 GB build and then repeat it, and it must not report failure
    # either: skipping because the work is already in hand is the correct
    # outcome, not an error. It says so out loud, because a silent skip is how
    # a backup stops running without anyone noticing.
    if [[ "${MODE}" == "run" ]]; then
      LOCKFILE="${IMAGE_LOCK_FILE:-${REPO_ROOT}/store/.backup-images.lock}"
      exec {LOCKFD}>"${LOCKFILE}"
      if ! flock -n "${LOCKFD}"; then
        echo "kepek: MAR FUT egy masik epites (${LOCKFILE}) -- kihagyom"
        exit 0
      fi
    fi

    TMP="$(mktemp -t claudeclaw-img.XXXXXX)"
    trap 'rm -f "${TMP}"' EXIT
    read -r COUNT BYTES SUM <<<"$(fingerprint "${TMP}")"
    MB="$(awk -v b="${BYTES}" 'BEGIN{printf "%.1f", b/1048576}')"

    OLD_SUM="$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("fingerprint",""))
except Exception: print("")' "${STATE}" 2>/dev/null || echo "")"

    if [[ "${SUM}" == "${OLD_SUM}" && "${FORCE}" != "--force" ]]; then
      echo "kepek: valtozatlan (${COUNT} fajl, ${MB} MB) -- nincs teendo"
      exit 0
    fi

    # A CHANGE IS NOT A REASON TO RE-PACK EVERYTHING. Until 2026-09-26 the
    # archive was all-or-nothing: one new avatar re-packed and re-uploaded every
    # picture. The owner stopped that twice -- 2026-09-23 21:16 ("miert kell az
    # egeszet elmenteni, csak azert mert csinaltunk par uj kepet") and again on
    # 09-25 20:07, after 546 MB of new drawings pushed the whole 3.7 GB to Drive.
    #
    # SO ONCE A BASE EXISTS, A RUN SHIPS ONLY THE NEW FILES, as a supplement
    # (kepek-kieg-<stamp>.tar.gz) beside the base in the same Drive folder. The
    # state's `covered` map GROWS with each supplement instead of being replaced,
    # which is what lets backup.sh keep answering its per-file question exactly
    # as before. A restore is the base plus every supplement after it, extracted
    # in date order (a re-rendered file lands later and overwrites the old one).
    # drive_upload.py pins the newest base and everything newer, so a supplement
    # can never outlive the base it depends on, nor the reverse.
    #
    # A full rebuild happens only when there is no base yet, or on --force.
    #
    # THE THRESHOLD IS NOW 0 BY DEFAULT, and that follows from the change above:
    # the old 500 MB line was the break-even against a fixed 3.2 GB rebuild. A
    # supplement costs exactly the new bytes once, while waiting costs those
    # same bytes in every nightly diff until then -- so uploading any uncovered
    # file at the weekly run is never the more expensive side. The variable
    # stays so a test (or the owner) can still hold it back.
    REBUILD_MB="${IMAGE_REBUILD_MB:-0}"
    # Writes the uncovered paths to ${TMP}.loose and prints "<count> <MB>".
    read -r LOOSE_COUNT UNCOVERED_MB HAS_BASE <<<"$(python3 - "${STATE}" "${TMP}" "${TMP}.loose" <<'PY'
import json, sys
state, statfile, loosefile = sys.argv[1:4]
try:
    covered = json.load(open(state)).get("covered") or {}
except Exception:
    covered = {}
loose = 0
n = 0
with open(statfile, encoding="utf-8", errors="surrogateescape") as fh, \
     open(loosefile, "w", encoding="utf-8", errors="surrogateescape") as out:
    for line in fh:
        line = line.rstrip("\n")
        if not line:
            continue
        path, mtime, size = line.rsplit(" ", 2)
        if covered.get(path) != [int(mtime), int(size)]:
            loose += int(size)
            n += 1
            out.write(path + "\n")
print("%d %.1f %d" % (n, loose / 1048576.0, 1 if covered else 0))
PY
)"
    trap 'rm -f "${TMP}" "${TMP}.loose"' EXIT

    KIND=full
    if [[ -z "${OLD_SUM}" ]]; then
      echo "kepek: meg nincs mentve (${COUNT} fajl, ${MB} MB)"
    else
      echo "kepek: VALTOZOTT (${COUNT} fajl, ${MB} MB) -- ebbol ${LOOSE_COUNT} fajl, ${UNCOVERED_MB} MB nincs meg archivumban"
      if [[ "${FORCE}" != "--force" && "${HAS_BASE}" == "1" ]]; then
        KIND=kieg
        if [[ "${LOOSE_COUNT}" == "0" ]] \
           || awk -v u="${UNCOVERED_MB}" -v t="${REBUILD_MB}" 'BEGIN{exit !(u < t)}'; then
          echo "kepek: a kuszob ${REBUILD_MB} MB -- NEM epitek ujra, a napi mentes viszi oket"
          exit 0
        fi
        echo "kepek: csak az uj fajlok mennek fel, kiegeszito csomagban"
      fi
    fi
    [[ "${MODE}" == "status" ]] && exit 0

    STAMP="$(date +%Y%m%d-%H%M%S)"
    mkdir -p "${OUT_DIR}"
    # No gpg here, and that is a decision, not an omission: this archive holds
    # generated artwork and no credentials, and leaving it readable is what
    # makes it usable from Drive without a key. The nightly archive stays
    # encrypted because it carries tokens.
    if [[ "${KIND}" == "kieg" ]]; then
      ARCHIVE="${OUT_DIR}/kepek-kieg-${STAMP}.tar.gz"
      cp "${TMP}.loose" "${TMP}.paths"
      WANT="${LOOSE_COUNT}"
    else
      ARCHIVE="${OUT_DIR}/kepek-${STAMP}.tar.gz"
      list_images > "${TMP}.paths"
      WANT="${COUNT}"
    fi
    tar -czf "${ARCHIVE}" -T "${TMP}.paths"
    rm -f "${TMP}.paths"
    echo "kepek: kesz ${ARCHIVE} ($(wc -c < "${ARCHIVE}" | tr -d ' ') bajt)"

    # Verify before recording: a state file written from an unverified archive
    # would make every later run say "unchanged" about a copy that is not there.
    ENTRIES="$(tar -tzf "${ARCHIVE}" | wc -l | tr -d ' ')"
    if [[ "${ENTRIES}" -lt "${WANT}" ]]; then
      echo "kepek: HIBA -- az archivumban ${ENTRIES} bejegyzes van, de ${WANT} fajlt vartunk" >&2
      exit 6
    fi
    echo "kepek: ellenorizve, ${ENTRIES} bejegyzes"

    # --min-bytes 1: a supplement of a few line drawings is legitimately under
    # the 100 kB "suspiciously small" floor that guards the nightly backup.
    if ! python3 "${REPO_ROOT}/scripts/muhely/drive_upload.py" "${ARCHIVE}" \
         --folder "${DRIVE_FOLDER}" --keep 3 --min-bytes 1; then
      echo "kepek: a Drive-feltoltes ELBUKOTT -- az allapotfajlhoz NEM nyulok," >&2
      echo "kepek: igy a kovetkezo futas ujraprobalja." >&2
      exit 7
    fi

    # The per-file state is recorded too, not just the hash. backup.sh needs to
    # answer a sharper question than "did anything change?": for EACH file, is
    # THIS version of it already in an uploaded archive? Without that, a single
    # new picture would either keep all 1437 in the nightly backup, or -- far
    # worse -- let backup.sh skip a file that no image archive has yet.
    #
    # A full run REPLACES `covered`; a supplement MERGES into it, and only the
    # files it actually packed (${TMP}.loose), at the version it measured.
    python3 - "${STATE}" "${SUM}" "$(basename "${ARCHIVE}")" "${TMP}" "${KIND}" "${TMP}.loose" <<'PY'
import json, sys, time
state, sum_, archive, statfile, kind, loosefile = sys.argv[1:7]
stats = {}
with open(statfile, "r", encoding="utf-8", errors="surrogateescape") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line:
            continue
        # "path mtime size" -- the path may contain spaces, the two numbers may not.
        head, mtime, size = line.rsplit(" ", 2)
        stats[head] = [int(mtime), int(size)]
if kind == "kieg":
    old = json.load(open(state))
    files = old.get("covered") or {}
    packed = [l.rstrip("\n") for l in open(loosefile, encoding="utf-8",
                                            errors="surrogateescape") if l.strip()]
    for p in packed:
        files[p] = stats[p]
    base = old.get("archive")
    supplements = (old.get("supplements") or []) + [archive]
else:
    files = stats
    base = archive
    supplements = []
with open(state, "w") as fh:
    json.dump({"fingerprint": sum_, "files": len(files),
               "bytes": sum(v[1] for v in files.values()),
               "archive": base, "supplements": supplements,
               "saved_at": int(time.time()),
               "covered": files}, fh)
PY
    echo "kepek: allapot rogzitve (${STATE})"
    ;;

  *)
    echo "hasznalat: $(basename "$0") list|status|covered|mirror|run [--force]" >&2
    exit 64
    ;;
esac
