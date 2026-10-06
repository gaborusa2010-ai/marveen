#!/usr/bin/env python3
"""What did the last backup MISS?

The lateness watchdog answers a different question -- "did a backup run?" -- and
on 2026-09-03 it said RENDBEN all morning while four separate things turned out
not to be in the archive at all: the shared auto-memory folder (41 files), the
whole of scripts/hooks (66 entries, two of them live security gates), the
agents' reports, and a `repo/scripts` entry that LOOKED like coverage while only
scripts/muhely sat under it. An empty directory name in a manifest reads as
protection; that is worse than an obvious hole.

The root cause is not one forgotten path. backup.sh lists paths BY NAME, so
every new directory is excluded by default and nothing says so. This tool asks
the complementary question, per top-level directory:

    is there anything here that the backup does not have AND git does not have?

WHY GIT COUNTS AS COVER, AND ONLY PARTLY
A tracked file with no local modification can be restored from the repository,
so it does not need to be in the archive. That is real cover, but it stops at
this machine's disk: `git rev-list --count origin/main..HEAD` was 58 on
2026-09-03, i.e. dozens of commits exist on no remote. So this tool reports
git-cover as its own column and never folds it into "safe" -- a directory that
is only git-covered is one disk failure from the same loss. Deciding what to do
about that is card 55005337, not this script.

WHAT IT DELIBERATELY DOES NOT DO: it does not open the archive. The manifest is
written by backup.sh from the very list it archives (store/.last-backup-
manifest.txt), so reading it needs no decryption key and cannot drift from what
was taken. The cost is honest and stated: this reports on the LAST RUN. If the
manifest is missing or old, that is itself the finding, and the tool says so
instead of pretending to measure today.

Usage
-----
  backup-coverage.py            # human summary, exit 1 if anything is uncovered
  backup-coverage.py --json     # machine-readable, always exit 0
  backup-coverage.py --quiet    # print only when something is uncovered
"""
import argparse
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO, "store", ".last-backup-manifest.txt")
# THE SECOND ARCHIVE. Since 2026-09-22 the pictures and PDFs are not in the
# nightly archive at all -- they have their own (the owner's decision, Telegram
# msg 3536; scripts/backup-images.sh). A coverage report that only reads the
# nightly manifest would call 1426 files uncovered while they sit safely in an
# uploaded image archive, and a report that cries wolf about 1426 files is one
# nobody reads. So this file is read too, and it is the file the exclusion in
# backup.sh keys off, not a second hand-kept list of the same paths.
IMAGE_STATE = os.path.join(REPO, "store", "backup-images-state.json")
HOME = os.path.expanduser("~")

# Directories whose contents are rebuildable, enormous, or deliberately
# transient. Being on this list is a DECISION, not an oversight -- which is the
# whole point: an unlisted directory shows up as a finding instead of vanishing
# quietly. Keep the reason with the entry.
SKIP_REPO = {
    "node_modules",   # rebuilt by npm install
    "dist",           # build output
    ".git",           # the repository itself; backing it up into its own tree is circular
    "backups",        # the archives themselves
    "coverage",       # test output
    ".venv", "venv",
    "logs",           # runtime noise, rotated
    # Added 2026-10-03: store/scheduled-runs is in backup.sh STORE_SKIP since the
    # v1.40.0 merge (SCHEDPROMPTREF917 fire-time snapshots, 7-day disk retention,
    # regenerated on every fire). Without this line the checker reported it as
    # TELJESEN FEDETLEN on every run -- the checker's half of that ONE decision.
    "scheduled-runs",
}
SKIP_HOME_CLAUDE = {
    "projects",       # transcripts are runtime bulk; the memory/ subdirs ARE taken (see backup.sh)
    "cache", "tmp", "paste-cache", "downloads", "file-history",
    "shell-snapshots", "sessions", "session-env",
    "plugins",        # reinstalled from config
    "backups",
    # Both added 2026-09-19, after the lateness watchdog flagged them. Neither
    # is content: bridge-spawn holds one append-system-prompt.txt per remote
    # session, written by the harness and rewritten on the next spawn;
    # telemetry holds the 1p_failed_events.*.json queue a running process
    # writes about itself. Restoring either one would restore nothing.
    "bridge-spawn",
    "telemetry",
}
# Directory names skipped ANYWHERE in the walk. Each one is a decision backup.sh
# already made out loud, repeated here so the two do not disagree silently:
#   inbox/        -- "runtime/transient and intentionally excluded" (backup.sh)
#   .claude-config -- per-agent plugin/cache tree, reinstalled, and largely a symlink
#   __pycache__   -- byte-compiled, rebuilt on first import
# If one of these ever stops being transient, remove it HERE and the finding
# reappears. That is the point: an exclusion should cost a line of code, not be
# the default for everything nobody listed.
#   progress/     -- per-message seen-markers written by the channel bridge
#   .tmp-msg, .tmp-view -- scratch the agents write mid-turn and delete later
#   hook-backups  -- copies of files that are themselves backed up
SKIP_ANYWHERE = {
    "__pycache__", "inbox", ".claude-config", "node_modules", ".git",
    "progress", ".tmp-msg", ".tmp-view", "hook-backups",
}
# Files at the ~/.claude root that are deliberately NOT archived. Written down
# rather than silently skipped, because "we decided against it" and "nobody
# thought of it" look identical in a report otherwise:
#   .credentials.json -- an OAuth token. `claude login` reissues it, and one
#                        fewer place holding a live credential is worth more
#                        than the minute it saves.
#   history.jsonl     -- 5.4 MB of shell history; convenience, not state.
#   the rest          -- caches and last-run markers, regenerated on use.
SKIP_HOME_FILES = {
    ".credentials.json", "history.jsonl", ".last-cleanup",
    ".last-update-result.json", "mcp-needs-auth-cache.json",
    "remote-settings.json", "policy-limits.json",
}

# Runtime files skipped ANYWHERE, matched by suffix. Same reasoning as
# SKIP_ANYWHERE, one level down: these are not content, they are a running
# process describing itself.
#   .pid -- the channel bridge's process id, seven bytes, wrong the moment the
#           bot restarts. backup.sh excludes it by the same name; this list is
#           the checker's half of that ONE decision. Without it the tool
#           reported .claude/channels as a gap on every run, and a report that
#           is permanently red stops being read -- which is the failure this
#           whole tool exists to prevent.
SKIP_SUFFIXES_ANYWHERE = (".pid",)

# Suffixes skipped ONLY under the repo's store/ -- the checker's half of a rule
# backup.sh already states out loud in its store block:
#     "*.log, *.out, *.pid  -- runtime noise, worthless in a restore"
# Without it, 10 store logs came back uncovered on every single run (measured
# 2026-09-19), which is the .pid failure above repeating: a permanently red
# report stops being read.
#
# SCOPED, not global, and the scope is measured, not assumed (2026-09-19):
# outside store/ the tree holds 5 .log files -- four under
# agents/roxy/work/axolotls-space/ and ~/.claude/hooks/require-checked.log --
# and ALL FIVE are in the backup today. A global .log skip would blind the
# checker to the day one of them drops out. The skip matches the scope of the
# decision it mirrors, and no wider.
SKIP_SUFFIXES_STORE = (".log", ".out")


def is_backup_copy(name):
    """The .bak-YYYYMMDD / trailing-tilde copies the fleet leaves behind.

    Applied to files as well as directories: a stale `settings.json.bak-...`
    reported as "unprotected" is noise that hides the real finding next to it.
    """
    return (".bak-" in name or "-bak-" in name
            or name.endswith("~") or name.endswith(".pyc"))


def read_manifest(path=MANIFEST):
    """The paths the last backup took, split into its two groups.

    Returns (repo_paths, home_paths, mtime) or (None, None, None) when the
    manifest does not exist -- which is a finding, not an error.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
        mtime = os.path.getmtime(path)
    except OSError:
        return None, None, None
    repo, home, group = set(), set(), None
    for line in lines:
        if line.startswith("--- repo/"):
            group = repo
            continue
        if line.startswith("--- home/"):
            group = home
            continue
        if group is None or not line.strip():
            continue
        # Manifest lines are written as "repo/<rel>" / "home/<rel>".
        rel = line.split("/", 1)[1] if "/" in line else line
        group.add(rel)
    return repo, home, mtime


def read_image_state(path=IMAGE_STATE):
    """The repo-relative paths held by the last UPLOADED image archive.

    Only paths whose recorded mtime+size still match the file on disk count.
    That is the same test scripts/backup-images.sh `covered` applies and the
    same one backup.sh trusts when it drops a file from the nightly archive --
    so a picture re-rendered since the last image upload shows up here as
    uncovered, which is exactly what it is until one of the two archives
    catches up.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            covered = json.load(fh).get("covered") or {}
    except (OSError, ValueError):
        return set()
    out = set()
    for rel, want in covered.items():
        try:
            st = os.lstat(os.path.join(REPO, rel))
        except OSError:
            continue
        if [int(st.st_mtime), int(st.st_size)] == want:
            out.add(rel)
    return out


def covered_by(paths, rel):
    """Is `rel` inside something the backup took?

    A manifest entry can be a FILE or a DIRECTORY; a directory entry covers
    everything under it. Prefix matching is done on path segments, never on raw
    strings, so `scripts` does not appear to cover `scripts-old`.
    """
    if rel in paths:
        return True
    parts = rel.split("/")
    for i in range(1, len(parts)):
        if "/".join(parts[:i]) in paths:
            return True
    return False


def git_tracked_clean(root):
    """Files git can restore: tracked AND with no uncommitted local change.

    A modified tracked file is NOT cover -- the working copy is what would be
    lost, and git only holds the committed version.
    """
    try:
        tracked = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                                 capture_output=True, text=True, timeout=60)
        dirty = subprocess.run(["git", "-C", root, "diff", "--name-only", "-z", "HEAD"],
                               capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return set()
    if tracked.returncode != 0:
        return set()
    t = {p for p in tracked.stdout.split("\0") if p}
    d = {p for p in dirty.stdout.split("\0") if p}
    return t - d


def walk_files(base, top, skip_names, newer_than=None):
    """Every file under base/top, as paths relative to base.

    `newer_than` (a mtime) drops files CREATED SINCE the backup ran. Measured
    2026-09-04: an agent wrote a new skill at 08:45 while the manifest was from
    07:57, and it came back as "in no backup and in no git" -- true, and
    meaningless. Nothing was skipped; the file simply did not exist yet.
    Without this, every file written after the last backup becomes a finding,
    and the real one (a directory the backup does not know about at all) drowns
    in a list that grows all day.
    """
    out = []
    root_dir = os.path.join(base, top)
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames
                       if d not in skip_names and d not in SKIP_ANYWHERE and not is_backup_copy(d)]
        for f in filenames:
            if is_backup_copy(f):
                continue
            if f.endswith(SKIP_SUFFIXES_ANYWHERE):
                continue
            if base == REPO and top == "store" and f.endswith(SKIP_SUFFIXES_STORE):
                continue
            if dirpath == os.path.join(HOME, ".claude") and f in SKIP_HOME_FILES:
                continue
            full = os.path.join(dirpath, f)
            if os.path.islink(full):
                continue          # a symlink is not content; its target is measured where it lives
            if newer_than is not None:
                try:
                    if os.path.getmtime(full) > newer_than:
                        continue  # written after the backup: not a gap, just later
                except OSError:
                    pass
            out.append(os.path.relpath(full, base))
    return out


def scan(base, tops, taken, skip_names, tracked=None, group="repo", newer_than=None,
         in_images=None):
    """Per top-level directory: how many files, how many covered, by what.

    `in_images` is the set of repo-relative paths held by the image archive
    (empty for the home/ group, which has none). It is counted as backup cover,
    because it is: a second archive, uploaded to its own Drive folder, verified
    the same way. It gets its own column so the report never hides WHICH copy a
    file depends on.
    """
    images = in_images or set()
    rows = []
    for top in sorted(tops):
        files = walk_files(base, top, skip_names, newer_than)
        if not files:
            continue
        in_backup = [f for f in files if covered_by(taken, f)]
        rest = [f for f in files if f not in set(in_backup)]
        in_image = [f for f in rest if f in images]
        rest = [f for f in rest if f not in images]
        in_git = [f for f in rest if tracked and f in tracked]
        uncovered = [f for f in rest if not (tracked and f in tracked)]
        rows.append({
            "dir": top,
            "files": len(files),
            "in_backup": len(in_backup),
            "in_image_archive": len(in_image),
            "git_only": len(in_git),
            "uncovered": len(uncovered),
            "blind": blind_subdirs(base, group, files, uncovered),
            "examples": sorted(uncovered)[:4],
        })
    return rows


def blind_subdirs(base, group, all_files, uncovered):
    """Directories where NOTHING is covered, named exactly.

    A file-by-file list is the wrong output for this question -- 522 unprotected
    paths under agents/ tell you nothing you can decide about. What is
    actionable is "this directory is entirely outside the backup", because that
    is the shape the failure keeps taking: backup.sh lists paths BY NAME, so a
    NEW directory is missed WHOLE, never partially.

    Grouping is by the file's OWN directory, not by a fixed depth. A fixed depth
    was the first attempt and it lied in both directions within one run: at
    depth 2 `agents/anorak` looked partially covered (its CLAUDE.md is taken) so
    the fully-blind `agents/anorak/.claude/skills` under it never got named,
    while `.claude/agents` appeared twice because two different bases produce
    the same relative name. Hence also the `group` prefix on every row: repo/
    and home/ are separate trees and must not share a label.
    """
    if not uncovered:
        return []
    # Every file counts towards EVERY directory above it, not just its immediate
    # parent. Counting only the immediate parent was the second thing this
    # function got wrong: `store/idea-attachments/szemelyes` holds no files of
    # its own, only twelve hash-named subfolders, so it could never be seen as
    # blind and the report listed all twelve instead of the one folder they
    # share.
    def ancestors(path):
        parts = os.path.dirname(path).split("/")
        return ["/".join(parts[:i]) for i in range(1, len(parts) + 1) if parts[0]]

    have = {}
    for f in all_files:
        for d in ancestors(f):
            have.setdefault(d, [0, 0])[0] += 1
    for f in uncovered:
        for d in ancestors(f):
            have.setdefault(d, [0, 0])[1] += 1
    blind = {d for d, (total, bad) in have.items() if bad and bad >= total}
    # Report the HIGHEST blind directory, not every leaf under it. Without this
    # the twelve `store/idea-attachments/szemelyes/<hash>` folders each got
    # their own line and buried the finding they all belong to -- a report
    # nobody reads protects nothing.
    def parent_also_blind(d):
        parts = d.split("/")
        return any("/".join(parts[:i]) in blind for i in range(1, len(parts)))
    out = []
    for d in sorted(blind):
        if parent_also_blind(d):
            continue
        out.append({"dir": f"{group}/{d}" if d else group, "files": have[d][1]})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="print only when something is uncovered")
    a = ap.parse_args(argv)

    repo_taken, home_taken, mtime = read_manifest()
    if repo_taken is None:
        msg = ("NINCS MENTESI MANIFESZT (store/.last-backup-manifest.txt). "
               "A backup.sh 2026-09-04 ota irja ki; ha nincs, akkor azota nem futott le teljes mentes. "
               "Ez maga a lelet: a lefedettseg NEM MERHETO, es a 'nem talaltam hianyt' nem ugyanaz, "
               "mint a 'nincs hiany'.")
        if a.json:
            print(json.dumps({"ok": False, "reason": "no-manifest"}, ensure_ascii=False))
            return 0
        print(msg)
        return 1

    age_days = round((time.time() - mtime) / 86400, 1)
    tracked = git_tracked_clean(REPO)
    in_images = read_image_state()

    repo_tops = [d for d in os.listdir(REPO)
                 if os.path.isdir(os.path.join(REPO, d))
                 and d not in SKIP_REPO and not is_backup_copy(d)]
    repo_rows = scan(REPO, repo_tops, repo_taken, SKIP_REPO, tracked, group="repo",
                     newer_than=mtime, in_images=in_images)

    claude = os.path.join(HOME, ".claude")
    home_rows = []
    if os.path.isdir(claude):
        tops = [os.path.join(".claude", d) for d in os.listdir(claude)
                if os.path.isdir(os.path.join(claude, d))
                and d not in SKIP_HOME_CLAUDE and not is_backup_copy(d)]
        # No git under $HOME/.claude, so nothing there has a second copy.
        home_rows = scan(HOME, tops, home_taken, SKIP_HOME_CLAUDE, None, group="home", newer_than=mtime)

    bad = [r for r in repo_rows + home_rows if r["uncovered"] > 0]

    if a.json:
        print(json.dumps({
            "ok": not bad, "manifest_age_days": age_days,
            "repo": repo_rows, "home": home_rows,
        }, ensure_ascii=False, indent=1))
        return 0

    if a.quiet and not bad:
        return 0

    print(f"Mentes-lefedettseg. A manifeszt {age_days} napos "
          f"({time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))}).")
    # --quiet prints the FINDING only. Measured 2026-09-04, running the command
    # straight out of the scheduled task that would use it: the full table came
    # out too, twenty-odd rows of directories that are fine. A heartbeat that
    # prints a wall of green on every alert teaches its reader to skim, and then
    # the one line that mattered is skimmed too.
    if not a.quiet:
        print(f"{'konyvtar':34} {'fajl':>6} {'mentesben':>10} {'keparch':>8} {'csak git':>9} {'SEHOL':>7}")
        for r in repo_rows + home_rows:
            mark = "  <<<" if r["uncovered"] else ""
            print(f"{r['dir'][:34]:34} {r['files']:6} {r['in_backup']:10} {r.get('in_image_archive', 0):8} {r['git_only']:9} {r['uncovered']:7}{mark}")
    if bad:
        # One directory, one line. The home side scans `.claude/agents`,
        # `.claude/hooks` and friends as separate tops, so their common ancestor
        # `.claude` came back once per scan row and printed three times.
        merged = {}
        for r in bad:
            for b in r["blind"]:
                merged[b["dir"]] = max(merged.get(b["dir"], 0), b["files"])
        blind = [{"dir": d, "files": n} for d, n in merged.items()]
        if blind:
            print("\nTELJESEN FEDETLEN KONYVTARAK (sem mentes, sem tiszta git -- EGY fajl sem):")
            for b in sorted(blind, key=lambda x: -x["files"]):
                print(f"  {b['dir']:44} {b['files']:5} fajl")
        partial = [r for r in bad if not r["blind"]]
        if partial:
            print("\nRESZLEGES HIANY (a konyvtar nagyja fedve, de nem minden):")
            for r in partial:
                print(f"  {r['dir']}: {r['uncovered']} fajl, pl. {', '.join(r['examples'])}")
        print("\nA 'csak git' oszlop NEM megnyugvas: azok a fajlok egyetlen lemezen allnak, "
              "amig a helyi commitok nincsenek felkuldve (kartya 55005337).")
    else:
        print("\nMinden atnezett konyvtar fedve van mentessel vagy tiszta git-allapottal.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
