#!/usr/bin/env python3
"""Morning backup sentinel as a command task (no LLM). Card #648 (175784f9).

Replaces the mentes-lemaradas-ellenorzes heartbeat, whose SKILL.md lists the
four questions this answers. It does NOT run the backup (heti-mentes-cmd.py
does, 20:00) and on purpose shares no code with it: a check run by the same
code as the work breaks together with the work.

  1. store/.last-backup-ok exists, is not in the future, and is under 7 days
  2. backup-coverage.py: a directory that is in no backup and not clean in git.
     A NEW one needs judgment (is it content? add it to backup.sh?), which a
     script cannot give, so it wakes marveen with an inter-agent message and
     the round still exits 0. An unchanged list is old news: silent.
  3. the newest .gpg on Drive is the newest local .gpg, with the same size
  4. backup-due.py says due AND the last verified upload is over 26 h old:
     last night's run failed even though the 7-day age still looks fine
     (+ a .tar.gz from the last 48 h without its .gpg: an interrupted run)

Exit 0 = all fine (or only a new coverage gap, handed to marveen). A problem
exits 1 with ONE short line on stderr: the command-task runner forwards its
first 200 chars to the owner's Telegram (failThreshold 1), so that line is the
alert text.

    python3 scripts/mentes-orszem-cmd.py            the real round
    python3 scripts/mentes-orszem-cmd.py --no-drive skip question 3 (tests)
"""
import argparse, datetime, glob, json, os, subprocess, sys, time

ROOT = os.environ.get("MARVEEN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "store", ".last-backup-ok")
GAPS = os.path.join(ROOT, "store", "mentes-orszem-gaps.json")
LOG = os.path.join(ROOT, "store", "mentes-orszem-cmd.log")
BACKUPS = os.path.join(ROOT, "backups")
FOLDER = "Marveen Backups"


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


def last_upload_age_h(now):
    """1: hours since the last VERIFIED upload. Missing or junk is a failure,
    never 'fresh' (SKILL.md: a missing file is not a recent backup)."""
    try:
        last = int(open(STATE).read().strip())
    except (OSError, ValueError):
        raise Fail("nincs ellenorzott gepen kivuli mentes: a store/.last-backup-ok hianyzik vagy hibas")
    age_h = (now - last) / 3600
    if age_h < 0:
        raise Fail("a mentes allapotfajljaban jovobeli idobelyeg all, a mentes kora nem ismert")
    if age_h > 7 * 24:
        raise Fail(f"{age_h / 24:.1f} napja nincs ellenorzott gepen kivuli mentes")
    log(f"q1 ok: last verified upload {age_h:.1f} h ago")
    return age_h


def coverage(now):
    """2: returns the NEW uncovered dirs (not in yesterday's list)."""
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "backup-coverage.py"), "--json"],
                       cwd=ROOT, capture_output=True, text=True)
    try:
        d = json.loads(p.stdout)
    except ValueError:
        raise Fail(f"a mentes-lefedettseg ellenorzese nem futott le (exit {p.returncode})")
    age = d.get("manifest_age_days")
    if age is None or age > 7:
        raise Fail("a mentes manifeszt hianyzik vagy 7 napnal regebbi: nem futott teljes mentes")
    gaps = sorted(f"{k}/{r['dir']}" for k, v in d.items() if isinstance(v, list)
                  for r in v if r.get("uncovered"))
    try:
        prev = set(json.load(open(GAPS)))
    except (OSError, ValueError):
        prev = set()
    new = [g for g in gaps if g not in prev]
    log(f"q2: {len(gaps)} uncovered, {len(new)} new")
    return gaps, new


def save_gaps(gaps):
    """Only after the wake-up went out: a failed send must re-fire tomorrow."""
    tmp = GAPS + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(gaps, fh)
    os.replace(tmp, GAPS)


def drive_matches_local():
    """3: newest .gpg on Drive == newest local .gpg (name and size)."""
    local = sorted(glob.glob(os.path.join(BACKUPS, "claudeclaw-*.tar.gz.gpg")), key=os.path.getmtime)
    if not local:
        raise Fail("nincs helyi titkositott mentes a backups/ mappaban")
    newest = local[-1]
    sys.path.insert(0, os.path.join(ROOT, "scripts", "muhely"))
    try:
        import google_auth as g
        r = g.call("https://www.googleapis.com/drive/v3/files", params={
            "q": "name contains 'claudeclaw' and trashed=false", "orderBy": "createdTime desc",
            "pageSize": "3", "fields": "files(name,size,createdTime)"})
    except SystemExit as e:
        raise Fail(f"a mentes-orszem nem erte el a Drive-ot: {str(e).splitlines()[0][:100]}")
    except Exception as e:  # network errors surface as URLError, OSError
        raise Fail(f"a mentes-orszem nem erte el a Drive-ot: {type(e).__name__}")
    files = r.get("files") or []
    if not files:
        raise Fail("a Drive-on nincs egy mentes-archivum sem")
    top = files[0]
    name, size = os.path.basename(newest), os.path.getsize(newest)
    if top.get("name") != name:
        raise Fail(f"a Drive legfrissebb mentese ({top.get('name')}) nem a helyi legfrissebb ({name})")
    if int(top.get("size") or 0) != size:
        raise Fail(f"a Drive-masolat merete elter a helyitol: {name}")
    log(f"q3 ok: drive newest = local newest {name} ({size} bytes)")


def missed_night(age_h, now):
    """4: due now and the last verified upload is over 26 h old."""
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "backup-due.py")],
                       cwd=ROOT, capture_output=True, text=True)
    if "ESEDEKES" in p.stdout and age_h > 26:
        raise Fail(f"elbukott az esti mentes: esedekes volt, az utolso ellenorzott feltoltes {age_h / 24:.1f} napja")
    for f in sorted(glob.glob(os.path.join(BACKUPS, "claudeclaw-*.tar.gz"))):
        if not os.path.exists(f + ".gpg") and now - os.path.getmtime(f) < 48 * 3600:
            raise Fail(f"felbeszakadt mentes gyanuja: {os.path.basename(f)} titkositott par nelkul")
    log("q4 ok")


def wake_marveen(new):
    text = ("[mentes-orszem] Uj, mentesbol kimarado konyvtar (backup-coverage.py, #648): "
            + ", ".join(new[:10])
            + ". Nezd meg, mi van benne; ha tartalom, vedd fel a backup.sh-ba (store/ alatt nem kell, "
            "lasd mentes-lemaradas-ellenorzes SKILL.md). A pontos ut: backup-coverage.py --json.")
    p = subprocess.run(["bash", os.path.join(ROOT, "scripts", "agent-msg.sh"), "marveen", "marveen", text],
                       cwd=ROOT, capture_output=True, text=True)
    if "OK id=" not in p.stdout:
        raise Fail(f"uj mentesi res van ({new[0]}), de a jelzes marveennek nem ment ki")
    log(f"q2: marveen woken, {p.stdout.strip()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-drive", action="store_true")
    a = ap.parse_args()
    now = time.time()
    try:
        age_h = last_upload_age_h(now)
        missed_night(age_h, now)
        if not a.no_drive:
            drive_matches_local()
        gaps, new = coverage(now)
        if new:
            wake_marveen(new)
        save_gaps(gaps)
    except Fail as e:
        log(f"FAIL: {e}")
        print(str(e), file=sys.stderr)
        return 1
    log("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
