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
    python3 scripts/heti-mentes-cmd.py --resume-interrupted
                                                  rerun a run that was killed
                                                  half-way (#765, systemd timer)

Interrupted runs (#765, d02d5092): the runner replays a MISSED 20:00 slot on
startup, but a run that started and was then killed (2026-11-01: machine shut
down mid-backup) counts as fired, so nothing retried it for 24 hours. A due run
writes store/.backup-running and removes it when it ends, failed or not, while
holding a flock on store/.backup-run.lock. A marker with a free lock means the
run was killed; --resume-interrupted (systemd timer marveen-mentes-potlas,
hourly and after boot) starts it again, at most once. Not a scheduler task: a
command task's silent exit 0 after an alert would read as "recovered".
"""
import argparse, datetime, fcntl, glob, hashlib, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS = os.path.join(ROOT, "backups")
STATE = os.path.join(ROOT, "store", ".last-backup-ok")
PASSFILE = os.path.join(ROOT, "store", ".backup-passphrase")
LOG = os.path.join(ROOT, "store", "heti-mentes-cmd.log")
RUNNING = os.path.join(ROOT, "store", ".backup-running")
RUN_LOCK = os.path.join(ROOT, "store", ".backup-run.lock")
# One retry: a run killed by the 3 h timeout (a hung upload) is not retried forever.
RESUME_MAX_ATTEMPTS = 2
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
    # BACKUP_LOG: backup.sh names this file in its failure alert (#765).
    env = dict(os.environ, BACKUP_LOG=os.path.relpath(LOG, ROOT))
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)
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


def read_marker():
    try:
        with open(RUNNING) as fh:
            m = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}  # unreadable marker: still a sign of a run that did not end
    return m if isinstance(m, dict) else {}


def marker_attempt(m):
    try:
        return int(m.get("attempt", 1))
    except (TypeError, ValueError):
        return 1


def write_marker(attempt):
    with open(RUNNING, "w") as fh:
        json.dump({"pid": os.getpid(), "started": f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
                   "attempt": attempt}, fh)


def clear_marker():
    try:
        os.remove(RUNNING)
    except FileNotFoundError:
        pass


def take_run_lock(wait=False):
    """Flock held for the whole run; None if another run holds it (wait=False).

    The kernel drops it when the holder dies, so "lock free + marker present"
    is the proof of a killed run, with no pid guessing (a pid can be reused
    after a reboot, even by the resume process itself)."""
    fd = os.open(RUN_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def resume_interrupted():
    if read_marker() is None:
        return 0  # nothing was interrupted: silent, every hour
    lock = take_run_lock()
    if lock is None:
        return 0  # the run is still going
    try:
        m = read_marker()  # re-read under the lock: the holder may have just finished
        if m is None:
            return 0
        attempt = marker_attempt(m) + 1
        if attempt > RESUME_MAX_ATTEMPTS:
            clear_marker()
            raise Fail(f"a mentes {RESUME_MAX_ATTEMPTS}x egymas utan felbemaradt (inditva: {m.get('started', '?')}), "
                       "tobbszor nem inditom ujra")
        log(f"resume: a {m.get('started', '?')}-kor indult mentes felbemaradt, ujrainditas, {attempt}. probalkozas")
        # Count the attempt before anything else can be killed: a kill between
        # here and the backup body must not make the interruption forgotten,
        # and a step that keeps failing early still runs out of attempts.
        write_marker(attempt)
        try:
            return locked_run(attempt)
        except BaseException:
            # A failure before the body (backup-due.py, an OSError) is a failure,
            # not a kill: leave no marker, or the next hour reports a second
            # "interrupted twice". A real kill never reaches this line.
            clear_marker()
            raise
    finally:
        os.close(lock)


def nightly():
    lock = take_run_lock()
    if lock is None:
        # A resumed run is going. Wait for it instead of exiting 0: the runner
        # reads a quiet exit 0 as "recovered" while the resume may still fail.
        # After it, backup-due.py decides. A failed resume leaves the run due and
        # this run does the backup; after a good one, any change since (a new
        # message or card, minutes in this fleet) still makes it due, so this is
        # usually a short diff backup, not a skip.
        m = read_marker() or {}
        log(f"wait: a {m.get('started', '?')}-kor indult mentes meg fut, megvarom")
        lock = take_run_lock(wait=True)
    try:
        return locked_run(1)
    finally:
        os.close(lock)


def locked_run(attempt):
    due = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "backup-due.py")],
                         cwd=ROOT, capture_output=True, text=True)
    if due.returncode == 1:
        clear_marker()  # a good copy exists: an old interruption no longer matters
        return 0  # not due: silent, no log line either (every quiet evening)
    if due.returncode != 0:
        raise Fail(f"a backup-due.py hibaval allt le (exit {due.returncode})")
    log("due: " + " | ".join(due.stdout.strip().splitlines()[-3:]))

    write_marker(attempt)
    try:
        return run_backup()
    finally:
        # Reached on success and on Fail; NOT on a kill or a shutdown, which is the point.
        clear_marker()


def run_backup():
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
    ap.add_argument("--resume-interrupted", action="store_true")
    a = ap.parse_args()
    try:
        if a.verify_only:
            gpg_path = a.verify_only + ".gpg"
            if not os.path.exists(gpg_path):
                raise Fail(f"nincs ilyen fajl: {gpg_path}")
            check_decrypts(gpg_path, os.path.basename(a.verify_only))
            check_drive_md5(gpg_path)
            return 0
        if a.resume_interrupted:
            return resume_interrupted()
        return nightly()
    except Exception as e:
        if not a.resume_interrupted and not isinstance(e, Fail):
            raise  # the 20:00 runner alerts on the traceback's non-zero exit
        reason = str(e) if isinstance(e, Fail) else f"varatlan hiba: {type(e).__name__}: {str(e)[:120]}"
        log(f"FAIL: {reason}")
        print(f"Mentes: {reason}. Addig nincs friss gepen kivuli masolat.", file=sys.stderr)
        if a.resume_interrupted:
            # The resume runs from a systemd timer, which tells nobody about a
            # non-zero exit; the command-task runner's alert does not cover it.
            alert(f"[MENTES] A felbemaradt mentes potlasa elbukott: {reason}. "
                  "Nincs friss gepen kivuli masolat (store/heti-mentes-cmd.log).")
        return 1


def alert(text):
    """Queue a message to marveen; a lost alert at least leaves a log line."""
    try:
        p = subprocess.run(["bash", "scripts/agent-msg.sh", "marveen", "marveen", text],
                           cwd=ROOT, capture_output=True, text=True)
        ok = p.returncode == 0 and "OK id=" in (p.stdout or "")
        why = f"exit {p.returncode}: {((p.stdout or '') + (p.stderr or '')).strip()[-120:]}"
    except Exception as e:
        ok, why = False, f"{type(e).__name__}: {e}"
    if not ok:
        log(f"ALERT NOT QUEUED ({why}): {text}")


if __name__ == "__main__":
    sys.exit(main())
