#!/usr/bin/env python3
"""restore-drill.py (#719): it must pass a good chain AND fail every broken one.

Each case builds a throwaway tree (MARVEEN_ROOT) with its own passphrase, live
DB, key files and gpg-encrypted archives, then runs the real script.
"""
import os, shutil, sqlite3, subprocess, sys, tarfile, tempfile, time, json

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "restore-drill.py")
KEYS = ["CLAUDE.md", "store/autonomy-config.json", "store/backup-baseline.json"]
TASK = ".claude/scheduled-tasks/heti-mentes/task-config.json"

fails = 0
def check(name, cond, detail=""):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"\n       {detail[-400:]}"))
    fails += 0 if cond else 1


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, t TEXT)")
    con.execute("CREATE TABLE kanban_cards (id INTEGER PRIMARY KEY)")
    con.executemany("INSERT INTO memories (t) VALUES (?)", [("x",)] * rows)
    con.executemany("INSERT INTO kanban_cards DEFAULT VALUES", [()] * 5)
    con.commit(); con.close()


def setup(tmp):
    root, home = os.path.join(tmp, "root"), os.path.join(tmp, "home")
    for d in ("store", "backups"):
        os.makedirs(os.path.join(root, d))
    os.makedirs(os.path.dirname(os.path.join(home, TASK)))
    with open(os.path.join(root, "store", ".backup-passphrase"), "w") as fh:
        fh.write("test-pass\n")
    for k in KEYS:
        with open(os.path.join(root, k), "w") as fh:
            fh.write(f"content of {k}\n")
    with open(os.path.join(home, TASK), "w") as fh:
        fh.write("{}\n")
    make_db(os.path.join(root, "store", "claudeclaw.db"), 200)
    old = time.time() - 86400 * 2  # live files older than any archive -> sha compared
    for p in [os.path.join(root, k) for k in KEYS] + [os.path.join(home, TASK)]:
        os.utime(p, (old, old))
    return root, home


def archive(root, home, tmp, stamp, kind, members, passphrase="test-pass"):
    """members: archive name -> source path (or bytes)."""
    stage = tempfile.mkdtemp(dir=tmp)
    tgz = os.path.join(root, "backups", f"claudeclaw-{stamp}-{kind}.tar.gz")
    with tarfile.open(tgz, "w:gz") as tf:
        for name, src in members.items():
            if isinstance(src, bytes):
                p = os.path.join(stage, "blob"); open(p, "wb").write(src); src = p
            tf.add(src, arcname=name)
    pf = os.path.join(stage, "pf"); open(pf, "w").write(passphrase + "\n")
    subprocess.run(["gpg", "--batch", "--yes", "--quiet", "--passphrase-file", pf, "--symmetric",
                    "-o", tgz + ".gpg", tgz], check=True,
                   env=dict(os.environ, GNUPGHOME=os.path.join(tmp, "gnupg")))
    return tgz


def full_members(root, home, db=None):
    m = {f"repo/{k}": os.path.join(root, k) for k in KEYS}
    m[f"home/{TASK}"] = os.path.join(home, TASK)
    m["repo/store/claudeclaw.db"] = db or os.path.join(root, "store", "claudeclaw.db")
    return m


def run(root, home, tmp):
    env = dict(os.environ, MARVEEN_ROOT=root, HOME=home, GNUPGHOME=os.path.join(tmp, "gnupg"))
    p = subprocess.run([sys.executable, SCRIPT], env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def case(name, build, want_rc, want_text):
    tmp = tempfile.mkdtemp(prefix="rd-test-")
    os.makedirs(os.path.join(tmp, "gnupg"), mode=0o700)
    try:
        root, home = setup(tmp)
        build(root, home, tmp)
        rc, out = run(root, home, tmp)
        check(name, rc == want_rc and want_text in out, f"rc={rc}\n{out}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def good(root, home, tmp):
    archive(root, home, tmp, "20261101-200000", "full", full_members(root, home))

def good_chain(root, home, tmp):
    # the full's DB is empty; the newer diff carries the real one -> must use the diff
    empty = os.path.join(tmp, "empty.db"); make_db(empty, 0)
    archive(root, home, tmp, "20261101-200000", "full", full_members(root, home, db=empty))
    archive(root, home, tmp, "20261101-210000", "diff",
            {"repo/store/claudeclaw.db": os.path.join(root, "store", "claudeclaw.db")})

def older_diff_ignored(root, home, tmp):
    # a diff OLDER than the newest full is not part of the chain
    archive(root, home, tmp, "20261101-200000", "full", full_members(root, home))
    empty = os.path.join(tmp, "empty.db"); make_db(empty, 0)
    archive(root, home, tmp, "20261031-200000", "diff", {"repo/store/claudeclaw.db": empty})

def no_full(root, home, tmp):
    archive(root, home, tmp, "20261101-210000", "diff",
            {"repo/store/claudeclaw.db": os.path.join(root, "store", "claudeclaw.db")})

def wrong_pass(root, home, tmp):
    archive(root, home, tmp, "20261101-200000", "full", full_members(root, home), passphrase="other")

def corrupt_db(root, home, tmp):
    archive(root, home, tmp, "20261101-200000", "full",
            full_members(root, home, db=os.path.join(root, "CLAUDE.md")))

def rows_lost(root, home, tmp):
    small = os.path.join(tmp, "small.db"); make_db(small, 10)
    archive(root, home, tmp, "20261101-200000", "full", full_members(root, home, db=small))

def table_emptied(root, home, tmp):
    con = sqlite3.connect(os.path.join(tmp, "e.db"))
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, t TEXT)")
    con.execute("CREATE TABLE kanban_cards (id INTEGER PRIMARY KEY)")
    con.executemany("INSERT INTO memories (t) VALUES (?)", [("x",)] * 200)
    con.commit(); con.close()
    archive(root, home, tmp, "20261101-200000", "full",
            full_members(root, home, db=os.path.join(tmp, "e.db")))

def no_db(root, home, tmp):
    m = full_members(root, home); del m["repo/store/claudeclaw.db"]
    archive(root, home, tmp, "20261101-200000", "full", m)

def key_differs(root, home, tmp):
    m = full_members(root, home); m["repo/CLAUDE.md"] = b"something else\n"
    archive(root, home, tmp, "20261101-200000", "full", m)

def key_missing(root, home, tmp):
    m = full_members(root, home); del m["repo/store/autonomy-config.json"]
    archive(root, home, tmp, "20261101-200000", "full", m)

def key_changed_live(root, home, tmp):
    # live CLAUDE.md edited after the archive -> presence check only, still passes
    m = full_members(root, home); m["repo/CLAUDE.md"] = b"older text\n"
    archive(root, home, tmp, "20261101-200000", "full", m)
    os.utime(os.path.join(root, "CLAUDE.md"), None)

def chain_gap(root, home, tmp):
    archive(root, home, tmp, "20261101-200000", "full", full_members(root, home))
    with open(os.path.join(root, "store", "backup-baseline.json"), "w") as fh:
        json.dump({"archive": "claudeclaw-20261101-200000-full.tar.gz",
                   "chain": ["claudeclaw-20261101-210000-diff.tar.gz"]}, fh)

def damaged_archive(root, home, tmp):
    tgz = archive(root, home, tmp, "20261101-200000", "full", full_members(root, home))
    data = bytearray(open(tgz, "rb").read())
    pf = os.path.join(tmp, "pf"); open(pf, "w").write("test-pass\n")
    cut = tgz + ".cut"; open(cut, "wb").write(bytes(data[:len(data) // 2]))
    subprocess.run(["gpg", "--batch", "--yes", "--quiet", "--passphrase-file", pf, "--symmetric",
                    "-o", tgz + ".gpg", cut], check=True,
                   env=dict(os.environ, GNUPGHOME=os.path.join(tmp, "gnupg")))


case("good full -> pass", good, 0, "OK: restore drill passed (2 tables")
case("db only in the newer diff -> restored from the diff, pass", good_chain, 0, "db from 20261101-210000")
case("diff older than the full is ignored", older_diff_ignored, 0, "chain: 1 archives")
case("no full archive -> fail", no_full, 1, "nincs titkositott teljes mentes")
case("wrong passphrase -> fail", wrong_pass, 1, "nem fejtheto vissza")
case("not a database -> fail", corrupt_db, 1, "nem nyithato meg")
case("half the rows gone -> fail", rows_lost, 1, "sorok hianyoznak: memories 10/200")
case("table emptied -> fail", table_emptied, 1, "kanban_cards 0/5")
case("db missing from the archive -> fail", no_db, 1, "claudeclaw.db nincs benne")
case("key file differs from unchanged live -> fail", key_differs, 1, "kulcsfajl elter")
case("key file missing -> fail", key_missing, 1, "kulcsfajl hianyzik")
case("key file changed live since -> presence only, pass", key_changed_live, 0, "sha not compared")
case("baseline names a link that is not there -> fail", chain_gap, 1, "lanc-szem")
case("truncated archive -> fail", damaged_archive, 1, "Visszaallitasi proba:")

print("FAILED" if fails else "all ok")
sys.exit(1 if fails else 0)
