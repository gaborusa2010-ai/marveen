#!/usr/bin/env bash
# Pre-modification backup.
#
# RULE: before ANY system modification (schema change,
# feature deploy, config edit that the live service reads), snapshot the
# critical mutable state first. Code is already safe in git; this captures the
# state git does NOT track: the SQLite DB, the vault, and runtime config.
#
# Rolling retention: keep the newest $KEEP snapshots, prune the rest.
# Usage: scripts/pre-modify-backup.sh [label]
#   label is an optional short tag for the snapshot dir (e.g. "openrouter-ui").
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STORE="$REPO/store"

# Checksum tool, resolved ONCE and never assumed. sha256sum is GNU coreutils and
# does not exist on macOS, where the same job is `shasum -a 256`. This repo has
# learned that twice already -- limit-monitor.sh carries a comment about a bare
# md5sum returning an EMPTY hash there, and github-pr-monitor.sh one about BSD
# grep having no -P -- and this script still shipped the third instance: every
# manifest line here was written with an empty checksum on a mac while the run
# reported success. Empty is the dangerous shape, not absent: a manifest full of
# blank sums still looks like a manifest.
if command -v sha256sum >/dev/null 2>&1; then
  SHA_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
  SHA_CMD="shasum -a 256"
else
  SHA_CMD=""
fi
BKDIR="$STORE/backups"
KEEP=10
LABEL="${1:-manual}"
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$BKDIR/${TS}-${LABEL}"

mkdir -p "$DEST"

# Consistent SQLite snapshot (NOT a raw cp -- the live dashboard may be mid-write).
#
# The tool is resolved, never assumed. The `sqlite3` CLI is NOT installed on this
# install, and the first version of this block called it unconditionally: the
# snapshot silently did not happen while the run printed "backup ok" and exited 0.
# That is the same silent-success shape the checksum block below was written to
# kill, left standing on the HEAVIER of the two events -- the database is the
# whole reason this script exists, and losing it is not a lighter miss than
# losing a checksum column. python3 carries the same sqlite3 library in-process
# and its Connection.backup() is the same online-backup API as the CLI's
# `.backup`, so the fallback is equivalent, not a downgrade.
DB_SNAP_TOOL=""
if command -v sqlite3 >/dev/null 2>&1; then
  DB_SNAP_TOOL="sqlite3"
elif python3 -c 'import sqlite3' >/dev/null 2>&1; then
  DB_SNAP_TOOL="python3"
fi

DB_FAILED=0
DB_PRESENT=0
if [ -f "$STORE/claudeclaw.db" ]; then
  DB_PRESENT=1
  case "$DB_SNAP_TOOL" in
    sqlite3)
      sqlite3 "$STORE/claudeclaw.db" ".backup '$DEST/claudeclaw.db'" >/dev/null 2>&1
      ;;
    python3)
      SRC_DB="$STORE/claudeclaw.db" DST_DB="$DEST/claudeclaw.db" python3 - <<'PYSNAP' >/dev/null 2>&1
import os, sqlite3, sys
src, dst = os.environ["SRC_DB"], os.environ["DST_DB"]
try:
    s = sqlite3.connect("file:%s?mode=ro" % src, uri=True)
    d = sqlite3.connect(dst)
    with d:
        s.backup(d)
    d.close(); s.close()
except Exception as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
PYSNAP
      ;;
  esac
  # The claim is checked, not inferred from an exit code: a snapshot that left a
  # zero-byte file behind is worse than none, because the file looks like a backup.
  if [ -s "$DEST/claudeclaw.db" ]; then
    echo "  db: consistent snapshot ok ($DB_SNAP_TOOL)"
  else
    rm -f "$DEST/claudeclaw.db"
    DB_FAILED=1
    if [ -z "$DB_SNAP_TOOL" ]; then
      echo "  db: FAILED no sqlite snapshot tool found (neither the sqlite3 CLI nor python3 with the sqlite3 module)"
    else
      echo "  db: FAILED snapshot with '$DB_SNAP_TOOL' produced no file"
    fi
  fi
fi

# Small critical state git does not track. Explicit list -- store/ also holds
# ~1.7G of large/regenerable data we deliberately do NOT copy.
for f in vault.json .vault-key .dashboard-token \
         openrouter-models.json agents-desired.json autonomy-config.json \
         auto-restart.json command-task-health.json schedule-last-run.json; do
  [ -f "$STORE/$f" ] && cp -p "$STORE/$f" "$DEST/" 2>/dev/null
done

# Personal, untracked scripts -- the ones git will NOT bring back.
#
# These hold install-specific data (chat ids, absolute home paths, account-bound
# token refreshers), so they are deliberately never pushed upstream -- which
# means git can never restore them, and this folder is the ONLY copy. On
# 2026-07-26 a branch switch silently deleted pre-modify-backup.sh itself (it
# lived only on a feature branch); nothing errored, it was simply gone.
#
# WHICH files count as personal is per-install, so the list is data, not code:
# store/personal-scripts.txt, one repo-relative path per line (# comments and
# blank lines ignored). With no such file we fall back to every untracked,
# executable script git already knows nothing about -- which is exactly the set
# at risk. Either way the manifest makes the post-update check possible: compare
# the live tree against personal-scripts/MANIFEST.txt after any update or
# branch switch.
PERSONAL_DIR="$DEST/personal-scripts"
PERSONAL_LIST="$STORE/personal-scripts.txt"
mkdir -p "$PERSONAL_DIR"
: > "$PERSONAL_DIR/MANIFEST.txt"
PERSONAL_MISSING=0
PERSONAL_SAVED=0
PERSONAL_NOSUM=0

if [ -f "$PERSONAL_LIST" ]; then
  PERSONAL_FILES="$(grep -vE '^\s*(#|$)' "$PERSONAL_LIST")"
else
  # Untracked files under scripts/ are by definition the ones git cannot restore.
  PERSONAL_FILES="$(git -C "$REPO" ls-files --others --exclude-standard -- scripts/ 2>/dev/null)"
fi

for rel in $PERSONAL_FILES; do
  if [ -f "$REPO/$rel" ]; then
    mkdir -p "$PERSONAL_DIR/$(dirname "$rel")"
    cp -p "$REPO/$rel" "$PERSONAL_DIR/$rel" 2>/dev/null
    sum=""
    [ -n "$SHA_CMD" ] && sum="$($SHA_CMD "$REPO/$rel" 2>/dev/null | cut -d' ' -f1)"
    if [ -n "$sum" ]; then
      printf '%s  %s\n' "$sum" "$rel" >> "$PERSONAL_DIR/MANIFEST.txt"
    else
      # The copy is safe; only the drift check is lost. Say WHICH, in the file
      # itself: a blank checksum column reads as a manifest, an explicit NOSUM
      # does not.
      printf 'NOSUM  %s\n' "$rel" >> "$PERSONAL_DIR/MANIFEST.txt"
      PERSONAL_NOSUM=$((PERSONAL_NOSUM + 1))
    fi
    PERSONAL_SAVED=$((PERSONAL_SAVED + 1))
  else
    # Only reachable via an explicit list: a named file that is already gone is
    # the exact loss this backup exists to catch, so say so loudly.
    printf 'MISSING  %s\n' "$rel" >> "$PERSONAL_DIR/MANIFEST.txt"
    PERSONAL_MISSING=$((PERSONAL_MISSING + 1))
  fi
done
if [ "$PERSONAL_MISSING" -gt 0 ]; then
  echo "  personal-scripts: WARNING $PERSONAL_MISSING file(s) ALREADY MISSING from the live tree ($PERSONAL_SAVED saved)"
else
  echo "  personal-scripts: $PERSONAL_SAVED saved + manifest"
fi
if [ "$PERSONAL_NOSUM" -gt 0 ]; then
  if [ -z "$SHA_CMD" ]; then
    echo "  personal-scripts: WARNING no checksum tool found (neither sha256sum nor shasum) -- $PERSONAL_NOSUM path(s) recorded as NOSUM"
  else
    echo "  personal-scripts: WARNING $PERSONAL_NOSUM path(s) could not be checksummed with '$SHA_CMD' -- recorded as NOSUM"
  fi
fi

# Code rollback reference (the code itself lives in git).
git -C "$REPO" rev-parse HEAD          > "$DEST/git-HEAD.txt"    2>/dev/null
git -C "$REPO" branch --show-current   > "$DEST/git-branch.txt"  2>/dev/null

# Rotate: keep the newest $KEEP snapshot dirs, remove older ones.
if [ -d "$BKDIR" ]; then
  ls -1dt "$BKDIR"/*/ 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -rf "$old" && echo "  pruned old snapshot: $(basename "$old")"
  done
fi

SIZE="$(du -sh "$DEST" 2>/dev/null | cut -f1)"

# TWO events of different weight, kept apart. The snapshot above either happened
# or did not; the manifest is what makes a LATER comparison possible. A missing
# checksum must not throw away a good snapshot -- but it must not be reported as
# a clean run either, and until now it was: the run printed 18 "command not
# found" lines to stderr, then "backup ok" and exit 0. A scheduled caller reads
# the exit code, not the stderr, so every round would have looked perfect while
# the drift check quietly did not exist.
#
# THREE events now, and the database one is the heaviest: without it the snapshot
# does not hold the state this script was written to protect, so it reports
# FAILED and its own exit code. Reported together when both are true, because a
# caller that only ever sees the first line would otherwise fix the lighter one.
if [ "$DB_FAILED" = 1 ]; then
  echo "backup FAILED: $DEST ($SIZE, retain newest $KEEP)"
  echo "  the live store/claudeclaw.db was NOT snapshotted -- the other files were saved,"
  echo "  but the database is the state git cannot restore. This snapshot is NOT a rollback point."
  [ "$PERSONAL_NOSUM" -gt 0 ] && echo "  and $PERSONAL_NOSUM manifest path(s) carry NO checksum."
  exit 4
fi
if [ "$PERSONAL_NOSUM" -gt 0 ]; then
  echo "backup INCOMPLETE: $DEST ($SIZE, retain newest $KEEP)"
  echo "  the snapshot IS written, but $PERSONAL_NOSUM manifest path(s) carry NO checksum:"
  echo "  the post-update comparison can only prove EXISTENCE for those, not content."
  exit 3
fi
echo "backup ok: $DEST ($SIZE, retain newest $KEEP)"
exit 0
