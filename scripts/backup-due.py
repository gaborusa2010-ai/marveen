#!/usr/bin/env python3
"""Is a backup due tonight?

Two independent reasons, OR-ed. Exit 0 = due, exit 1 = not due.

  TIME     the last verified upload is older than 6.5 days. This is the floor:
           even a completely quiet week gets one archive.
  CONTENT  something worth losing changed since that upload. Added on the
           owner's request, 2026-09-06: "minden este ellenorizze le magat es ha
           volt fontos valtozas akkor csinaljon mentest automatikusan".

Why CONTENT is not simply "anything changed": nearly every file under the
project changes every day (logs, .bak files, caches), so a naive check would
fire daily and the condition would carry no information. The paths below were
chosen because losing them costs something that cannot be reproduced:

  - the database          measured in ROWS, not file mtime: the .db mtime is
                          refreshed by backup.sh itself and the -wal is newer
                          than the backup almost always, so both are false
                          sensors -- one never fires, the other always does
  - the invoice ledger    continuous numbering HMRC requires
  - scripts/ and src/     the code, much of it not yet pushed anywhere
  - the skill tree        the fleet's own learning
  - scheduled tasks       what the fleet does without being asked
  - the shared memory     the pages every agent loads
  - new git commits       measured 2026-09-06: 58 commits existed only here

Measured when this was written (50 minutes after a fresh backup): 3 changed
memory pages and 1 new commit -- so the check discriminates rather than always
firing. A quiet evening really does exit 1.
"""
import os, re, subprocess, sys, time

def project_slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
SLUG = project_slug(ROOT)
STATE = os.path.join(ROOT, "store", ".last-backup-ok")
FLOOR_DAYS = 6.5

WATCH = [
    (os.path.join(ROOT, "store", "muhely-invoice-log.json"),"szamla-sorszamozas"),
    (os.path.join(ROOT, "store", "muhely-counters.json"),   "muhely-szamlalok"),
    (os.path.join(ROOT, "scripts"),                         "scripts/"),
    (os.path.join(ROOT, "src"),                             "src/"),
    (os.path.join(HOME, ".claude", "skills"),               "kozos skill-fa"),
    (os.path.join(HOME, ".claude", "scheduled-tasks"),      "utemezett feladatok"),
    (os.path.join(HOME, ".claude", "projects",
                  SLUG, "memory"),                           "kozos memoria"),
]


def newer_than(path, cutoff):
    """How many files under `path` were modified after `cutoff`."""
    if not os.path.exists(path):
        return 0
    if os.path.isfile(path):
        try:
            return 1 if os.path.getmtime(path) > cutoff else 0
        except OSError:
            return 0
    n = 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            try:
                if os.path.getmtime(os.path.join(root, name)) > cutoff:
                    n += 1
            except OSError:
                continue
    return n


def main():
    try:
        last = int(open(STATE).read().strip())
    except Exception:
        # No state file is NOT the same as a fresh backup. Treat it as due and
        # say so -- a missing file used to read as "everything is fine".
        print("NINCS allapotfajl (store/.last-backup-ok) -> a mentes esedekes.")
        return 0

    age_days = (time.time() - last) / 86400.0
    print("utolso ellenorzott feltoltes: %s (%.1f nap)"
          % (time.strftime("%Y-%m-%d %H:%M", time.localtime(last)), age_days))

    reasons = []
    if age_days >= FLOOR_DAYS:
        reasons.append("IDO: %.1f nap telt el, a padlo %.1f" % (age_days, FLOOR_DAYS))

    changed = []
    for path, label in WATCH:
        n = newer_than(path, last)
        if n:
            changed.append((label, n))
    if changed:
        reasons.append("TARTALOM: " + ", ".join("%s (%d fajl)" % (l, n) for l, n in changed))

    # Az adatbazis NEM fajl-mtime-mal merheto. A .db mtime-jat maga a backup.sh
    # frissiti (wal_checkpoint), a -wal pedig szinte mindig ujabb a mentesnel --
    # tehat mindket fajl HAMIS erzekelo: az egyik sosem szol, a masik mindig.
    # Ezert a valodi tartalmat kerdezzuk: hany SOR keletkezett a mentes ota.
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "store", "claudeclaw.db"),
                              uri=True, timeout=5)
        db_rows = []
        # A TABLANEVEKET 2026-09-06-an visszamertem a sqlite_master-bol. Az elso
        # valtozatban ketto koszul ("messages", "daily_log") NEM LETEZETT, es a
        # `except sqlite3.Error: continue` NEMAN atlepte oket -- vagyis a check
        # ket erzekelo nelkul futott volna, es "nulla valtozast" mondott volna.
        # Ezert a hianyzo tabla mostantol JELENTES, nem csend.
        for tabla, oszlop in (("memories", "created_at"), ("agent_messages", "created_at"),
                              ("kanban_cards", "updated_at"), ("daily_logs", "created_at"),
                              ("idea_box", "updated_at"), ("kanban_comments", "created_at")):
            try:
                n = con.execute("SELECT COUNT(*) FROM %s WHERE %s > ?" % (tabla, oszlop),
                                (last,)).fetchone()[0]
            except sqlite3.Error as exc:
                db_rows.append("%s NEM MERHETO (%s)" % (tabla, exc))
                continue
            if n:
                db_rows.append("%s (%d sor)" % (tabla, n))
        con.close()
    except Exception as exc:
        # Egy nem merheto adatbazis nem ugyanaz, mint egy valtozatlan adatbazis.
        db_rows = ["adatbazis NEM MERHETO: %s" % exc]
    if db_rows:
        reasons.append("ADATBAZIS: " + ", ".join(db_rows))

    try:
        out = subprocess.run(["git", "-C", ROOT, "log", "--since", "@%d" % last,
                              "--oneline"], capture_output=True, text=True, timeout=20)
        commits = [l for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        commits = []
    if commits:
        reasons.append("GIT: %d uj commit" % len(commits))

    if reasons:
        for r in reasons:
            print("  ->", r)
        print("ESEDEKES")
        return 0
    print("  -> semmi fontos nem valtozott")
    print("NEM ESEDEKES")
    return 1


if __name__ == "__main__":
    sys.exit(main())
