#!/usr/bin/env python3
"""Monthly restore drill (#719): prove the backups can be restored, not only listed.

Why this exists: until 2026-11 nothing ever restored a backup. The nightly
check (heti-mentes-cmd.py) decrypts and LISTS the archive and compares the
Drive md5; backup.sh verifies the archive against its manifest. Neither opens
the restored database. A backup that lists fine but holds an empty or corrupt
claudeclaw.db would pass both.

What the drill does, on the ENCRYPTED copies (the .gpg is what goes to Drive,
and the nightly md5 check ties it to the Drive bytes, so restoring from it also
proves the passphrase still works):

  1. Picks the restore chain: the newest *-full.tar.gz.gpg in backups/, then
     every *-diff.tar.gz.gpg stamped after it, oldest first. If the baseline
     (store/backup-baseline.json) descends from the same full, every link it
     names must be present locally, or the chain has a gap.
  2. Walks the chain newest-first and extracts claudeclaw.db plus a few key
     files into a temp dir, each from the newest archive that carries it
     (an incremental only carries what changed, so this IS the restored state).
  3. claudeclaw.db: PRAGMA integrity_check, then a row count per table,
     compared with the live DB opened read-only. It prints how many tables it
     compared, so "0 compared" can never pass as success.
  4. Key files: sha256 against the live file when the live file has not been
     touched since the archive was made; otherwise presence + non-empty only,
     and the report says which check ran.

What it cannot see, stated rather than hidden: an incremental archive cannot
express a deletion (scripts/backup-delta.py), so a file deleted after the full
comes back on restore. Row counts below the live ones are expected (the live DB
moved on since the archive); the drill fails only on a drop no week-old backup
explains (see TABLE_DROP_*).

Exit 0 = drill passed, 1 = failed (one owner-readable line on stderr, which the
command-task runner turns into the Telegram alert), 2 = usage error.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

# MARVEEN_ROOT: run a worktree copy against the live tree (tests, review).
ROOT = os.environ.get("MARVEEN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS = os.path.join(ROOT, "backups")
BASELINE = os.path.join(ROOT, "store", "backup-baseline.json")
PASSFILE = os.path.join(ROOT, "store", ".backup-passphrase")
LIVE_DB = os.path.join(ROOT, "store", "claudeclaw.db")
LOG = os.path.join(ROOT, "store", "restore-drill.log")

DB_MEMBER = "repo/store/claudeclaw.db"
# Archive member -> live path. Small, slow-changing files the whole fleet
# depends on; the sha check only bites when the live copy is unchanged.
KEY_FILES = {
    "repo/CLAUDE.md": os.path.join(ROOT, "CLAUDE.md"),
    "repo/store/autonomy-config.json": os.path.join(ROOT, "store", "autonomy-config.json"),
    "repo/store/backup-baseline.json": BASELINE,
    "home/.claude/scheduled-tasks/heti-mentes/task-config.json":
        os.path.expanduser("~/.claude/scheduled-tasks/heti-mentes/task-config.json"),
}

# A table fails when the restored copy has lost more than this share of the
# live rows, and the live table is big enough for the share to mean something.
TABLE_DROP_SHARE = 0.5
TABLE_DROP_MIN_LIVE = 100

STAMP = re.compile(r"claudeclaw-(\d{8}-\d{6})-(full|diff)\.tar\.gz\.gpg$")


class Fail(Exception):
    """One short, owner-readable reason. It becomes the Telegram alert."""


def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stamp_time(stamp):
    return datetime.datetime.strptime(stamp, "%Y%m%d-%H%M%S").timestamp()


def pick_chain(backup_dir, baseline_path):
    """Newest full + every later diff, oldest first. Returns [(path, stamp, kind)]."""
    found = []
    for p in glob.glob(os.path.join(backup_dir, "claudeclaw-*.tar.gz.gpg")):
        m = STAMP.search(os.path.basename(p))
        if m:
            found.append((p, m.group(1), m.group(2)))
    fulls = sorted((f for f in found if f[2] == "full"), key=lambda f: f[1])
    if not fulls:
        raise Fail("nincs titkositott teljes mentes a backups mappaban")
    full = fulls[-1]
    diffs = sorted((f for f in found if f[2] == "diff" and f[1] > full[1]), key=lambda f: f[1])
    chain = [full] + diffs

    try:
        with open(baseline_path) as fh:
            base = json.load(fh)
    except (OSError, ValueError):
        base = None
    full_name = os.path.basename(full[0])[:-len(".gpg")]
    if base and base.get("archive") == full_name:
        have = {os.path.basename(p)[:-len(".gpg")] for p, _, _ in chain}
        missing = [n for n in base.get("chain") or [] if n not in have]
        if missing:
            raise Fail(f"hianyzik {len(missing)} lanc-szem a mentesbol (pl. {missing[0]}), "
                       "a visszaallitas hianyos lenne")
    elif base:
        log(f"note: baseline descends from {base.get('archive')}, newest full is {full_name}")
    return chain


def extract(gpg_path, members, dest):
    """Stream-decrypt one archive and pull out whichever of `members` it has."""
    dec = subprocess.Popen(["gpg", "--batch", "--quiet", "--passphrase-file", PASSFILE,
                            "-d", gpg_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    tar = subprocess.Popen(["tar", "-xzf", "-", "-C", dest, *members],
                           stdin=dec.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    dec.stdout.close()
    _, tar_err = tar.communicate()
    dec_err = dec.stderr.read()
    dec.wait()
    if dec.returncode != 0:
        raise Fail(f"a mentes nem fejtheto vissza: {os.path.basename(gpg_path)} "
                   f"(gpg {dec.returncode}: {dec_err.decode(errors='replace').strip()[:80]})")
    # tar exits 2 when a requested member is absent; that is normal for an
    # incremental. Any other complaint means a damaged archive.
    other = [l for l in tar_err.decode(errors="replace").splitlines()
             if "Not found in archive" not in l and "Exiting with failure" not in l and l.strip()]
    if other:
        raise Fail(f"a mentes kicsomagolasa hibas: {os.path.basename(gpg_path)}: {other[0][:80]}")
    return [m for m in members if os.path.exists(os.path.join(dest, m))]


def table_counts(db_path, readonly):
    uri = f"file:{db_path}?mode=ro" if readonly else f"file:{db_path}"
    con = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        con.close()


def check_db(restored, live):
    con = sqlite3.connect(restored)
    try:
        res = [r[0] for r in con.execute("PRAGMA integrity_check")]
    except sqlite3.DatabaseError as e:
        raise Fail(f"a visszaallitott adatbazis nem nyithato meg ({e})")
    finally:
        con.close()
    if res != ["ok"]:
        raise Fail(f"a visszaallitott adatbazis serult (integrity_check: {res[0][:60]})")
    log("check db: integrity_check ok")

    got = table_counts(restored, readonly=False)
    now = table_counts(live, readonly=True)
    compared, bad, notes = 0, [], []
    for name in sorted(now):
        if name not in got:
            notes.append(f"{name}: not in the backup (newer table?)")
            continue
        compared += 1
        r, l = got[name], now[name]
        if l >= TABLE_DROP_MIN_LIVE and r < l * TABLE_DROP_SHARE:
            bad.append(f"{name} {r}/{l}")
        elif l > 0 and r == 0:
            bad.append(f"{name} 0/{l}")
        if r != l:
            notes.append(f"{name}: backup {r}, live {l}")
    for name in sorted(set(got) - set(now)):
        notes.append(f"{name}: only in the backup (dropped since?)")
    for n in notes:
        log(f"  {n}")
    log(f"check db: {compared} tables compared, {len(got)} in the backup, {len(now)} live")
    if compared == 0:
        raise Fail("egyetlen tablat sem tudtam osszevetni a visszaallitott adatbazisban")
    if bad:
        raise Fail(f"a visszaallitott adatbazisbol sorok hianyoznak: {', '.join(bad[:3])}")
    return compared


def check_key_files(found, dest):
    """found: member -> archive stamp it came from."""
    for member, live in KEY_FILES.items():
        if member not in found:
            raise Fail(f"kulcsfajl hianyzik a mentesbol: {member.split('/', 1)[1]}")
        restored = os.path.join(dest, member)
        if os.path.getsize(restored) == 0:
            raise Fail(f"kulcsfajl ures a mentesben: {member.split('/', 1)[1]}")
        if os.path.exists(live) and os.path.getmtime(live) <= stamp_time(found[member]):
            if sha256(restored) != sha256(live):
                raise Fail(f"kulcsfajl elter a mentesben: {member.split('/', 1)[1]}")
            log(f"check file: {member} sha256 matches live")
        else:
            log(f"check file: {member} present ({os.path.getsize(restored)} bytes); "
                "live changed since the backup, sha not compared")


def drill(backup_dir, baseline_path, live_db, keep):
    chain = pick_chain(backup_dir, baseline_path)
    log(f"chain: {len(chain)} archives, " + ", ".join(os.path.basename(p) for p, _, _ in chain))
    dest = tempfile.mkdtemp(prefix="restore-drill-")
    os.chmod(dest, 0o700)  # the archive carries live secrets
    try:
        wanted = [DB_MEMBER, *KEY_FILES]
        found = {}
        for path, stamp, _ in reversed(chain):
            todo = [m for m in wanted if m not in found]
            if not todo:
                break
            got = extract(path, todo, dest)
            for m in got:
                found[m] = stamp
            log(f"extract: {os.path.basename(path)} -> {len(got)} of {len(todo)}")
        if DB_MEMBER not in found:
            raise Fail("a claudeclaw.db nincs benne a mentesben")
        log(f"db from {found[DB_MEMBER]}")
        n = check_db(os.path.join(dest, DB_MEMBER), live_db)
        check_key_files(found, dest)
        log(f"OK: restore drill passed ({n} tables, {len(KEY_FILES)} key files, "
            f"{len(chain)} archives in the chain)")
        return n
    finally:
        if keep:
            log(f"kept: {dest}")
        else:
            shutil.rmtree(dest, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backup-dir", default=BACKUPS)
    ap.add_argument("--baseline", default=BASELINE)
    ap.add_argument("--live-db", default=LIVE_DB)
    ap.add_argument("--keep", action="store_true", help="leave the restored temp dir in place")
    a = ap.parse_args()
    log("start: restore drill")
    try:
        drill(a.backup_dir, a.baseline, a.live_db, a.keep)
        return 0
    except Fail as e:
        log(f"FAIL: {e}")
        print(f"Visszaallitasi proba: {e}.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
