#!/usr/bin/env python3
"""Nightly backup as a command task (no LLM). Card #648 (175784f9).

Replaces the heti-mentes heartbeat, whose SKILL.md this follows step by step:

  0. backup-due.py           exit 1 = nothing to save -> exit 0, silent
  1. backup.sh, gpg, drive_upload.py, backup-images.sh run   (this order, see SKILL.md)
  2. four checks, each on its own evidence, not on an exit code:
       a. a NEW .tar.gz exists and is over 100 kB
       b. the .gpg decrypts and lists (full: > 50 entries; diff: no floor)
       c. drive_upload.py printed "feltoltve:"
       d. the Drive copy's md5Checksum equals the local .gpg's md5
  3. store/.last-backup-ok is written ONLY if all of that passed.

Exit 0 = saved and verified, or not due. Any failure exits non-zero with ONE
short line on stderr: the command-task runner forwards its first 200 chars to
the owner's Telegram (failThreshold 1), so that line is the alert text.

Why a command task: the heartbeat spent an LLM turn every evening, the quota
gate held it back at 100 % weekly use (2026-10-26/27: no backup ran), and a
context-guard restart killed its background upload once (2026-10-03). A command
task bypasses the quota gate and does not live in a session.

    python3 scripts/heti-mentes-cmd.py            the real run
    python3 scripts/heti-mentes-cmd.py --verify-only <archive.tar.gz>
                                                  checks b + d on an existing
                                                  upload, writes nothing
"""
import argparse, datetime, glob, hashlib, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS = os.path.join(ROOT, "backups")
STATE = os.path.join(ROOT, "store", ".last-backup-ok")
PASSFILE = os.path.join(ROOT, "store", ".backup-passphrase")
LOG = os.path.join(ROOT, "store", "heti-mentes-cmd.log")
FOLDER = "Marveen Backups"

sys.path.insert(0, os.path.join(ROOT, "scripts", "muhely"))


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


def run(cmd, what):
    """Run a step with its output appended to the log. Returns stdout."""
    log(f"start: {what}")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    try:
        with open(LOG, "a") as fh:
            fh.write(p.stdout[-20000:] + p.stderr[-20000:])
    except OSError:
        pass
    if p.returncode != 0:
        tail = (p.stderr.strip().splitlines() or p.stdout.strip().splitlines() or ["?"])[-1]
        raise Fail(f"{what} elbukott (exit {p.returncode}): {tail.replace(' -- ', ': ')[:120]}")
    return p.stdout


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_decrypts(gpg_path, archive_name):
    """b: decrypt + list. Streams, so a 3 GB archive needs no temp space."""
    dec = subprocess.Popen(["gpg", "--batch", "--quiet", "--passphrase-file", PASSFILE,
                            "-d", gpg_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lst = subprocess.Popen(["tar", "-tzf", "-"], stdin=dec.stdout,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    dec.stdout.close()
    entries = sum(1 for _ in lst.stdout)
    lst.wait()
    dec.wait()
    if dec.returncode != 0 or lst.returncode != 0:
        raise Fail(f"a titkositott mentes nem fejtheto vissza (gpg {dec.returncode}, tar {lst.returncode})")
    if "-diff.tar.gz" not in archive_name and entries <= 50:
        raise Fail(f"a teljes mentesben csak {entries} bejegyzes van (legalabb 51 kell)")
    log(f"check b ok: {entries} entries")
    return entries


def check_drive_md5(gpg_path):
    """d: the bytes that reached Drive, not the uploader's exit code."""
    import google_auth as g
    name = os.path.basename(gpg_path)
    try:
        tok = g.access_token()
        r = g.call("https://www.googleapis.com/drive/v3/files", token=tok, params={
            "q": f"name='{name}' and trashed=false",
            "fields": "files(id,name,size,md5Checksum)"})
    except SystemExit as e:
        raise Fail(f"a Drive-ellenorzes nem erte el a Drive-ot: {str(e).splitlines()[0][:100]}")
    except Exception as e:  # network errors surface as URLError, OSError
        raise Fail(f"a Drive-ellenorzes nem erte el a Drive-ot: {type(e).__name__}")
    files = r.get("files") or []
    if not files:
        raise Fail(f"a feltoltott fajl nincs a Drive-on: {name}")
    local = md5(gpg_path)
    if not any(f.get("md5Checksum") == local for f in files):
        raise Fail(f"a Drive-masolat md5-je elter a helyitol: {name}")
    log(f"check d ok: md5 {local}")


def nightly():
    due = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "backup-due.py")],
                         cwd=ROOT, capture_output=True, text=True)
    if due.returncode == 1:
        return 0  # not due: silent, no log line either (every quiet evening)
    if due.returncode != 0:
        raise Fail(f"a backup-due.py hibaval allt le (exit {due.returncode})")
    log("due: " + " | ".join(due.stdout.strip().splitlines()[-3:]))

    before = set(glob.glob(os.path.join(BACKUPS, "*.tar.gz")))
    run(["bash", "scripts/backup.sh"], "a backup.sh")
    new = sorted(set(glob.glob(os.path.join(BACKUPS, "*.tar.gz"))) - before, key=os.path.getmtime)
    if not new:
        raise Fail("a backup.sh nem hozott letre uj archivumot")
    src = new[-1]
    size = os.path.getsize(src)
    if size <= 100_000:
        raise Fail(f"az uj archivum gyanusan kicsi ({size} bajt)")
    log(f"check a ok: {os.path.basename(src)} {size} bytes")

    gpg_path = src + ".gpg"
    run(["gpg", "--batch", "--yes", "--passphrase-file", PASSFILE, "--symmetric",
         "--cipher-algo", "AES256", "-o", gpg_path, src], "a titkositas (gpg)")
    check_decrypts(gpg_path, os.path.basename(src))

    out = run([sys.executable, "-u", "scripts/muhely/drive_upload.py", gpg_path], "a Drive-feltoltes")
    if "feltoltve:" not in out:
        raise Fail("a Drive-feltolto nem irta ki, hogy feltoltve")
    log("check c ok")
    check_drive_md5(gpg_path)

    # After the daily archive, never before (SKILL.md section 1: the order is the guard).
    run(["bash", "scripts/backup-images.sh", "run"], "a kepmentes (backup-images.sh)")

    with open(STATE, "w") as fh:
        fh.write(f"{int(time.time())}\n")
    log(f"OK: {os.path.basename(gpg_path)}, state file written")

    # The heartbeat told the owner once a month that the backup lives; keep that.
    today = datetime.date.today()
    if today.weekday() == 0 and today.day <= 7:
        subprocess.run(["bash", "scripts/agent-msg.sh", "marveen", "marveen",
                        f"[heti-mentes havi] A mentes el: utolso ellenorzott feltoltes ma, "
                        f"{os.path.basename(gpg_path)} ({os.path.getsize(gpg_path) / 1e6:.0f} MB). "
                        "Havi egysoros jelzes a gazdanak (SKILL.md KIMENET)."],
                       cwd=ROOT, capture_output=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-only", metavar="ARCHIVE")
    a = ap.parse_args()
    try:
        if a.verify_only:
            gpg_path = a.verify_only + ".gpg"
            if not os.path.exists(gpg_path):
                raise Fail(f"nincs ilyen fajl: {gpg_path}")
            check_decrypts(gpg_path, os.path.basename(a.verify_only))
            check_drive_md5(gpg_path)
            return 0
        return nightly()
    except Fail as e:
        log(f"FAIL: {e}")
        print(f"Mentes: {e}. Addig nincs friss gepen kivuli masolat.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
