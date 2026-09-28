#!/usr/bin/env python3
"""Incremental-backup bookkeeping for backup.sh.

Why this exists: until 2026-09-22 every nightly archive was a FULL copy. The
owner measured the consequence himself and asked for the change (Telegram
msg 3533, 2026-09-22 20:37: "Ami mar egyszer el van mentve azt nem kell
megegyszer elmenteni...csak azt mentsuk el ami valtozott,ami hozza lett
adva..."). Measured the same evening, on the tree backup.sh actually takes:

    the whole tree          5452 files   3371.7 MB
    changed in 24 hours      204 files    225.7 MB
    changed in 7 days       1023 files    518.8 MB

so roughly 93% of every nightly upload was a byte-for-byte repeat.

INCREMENTAL SINCE 2026-09-26 -- AND WHY IT WAS DIFFERENTIAL BEFORE.
From 2026-09-22 to 2026-09-26 every nightly archive was compared against the
last FULL backup (differential), so each one was self-sufficient: restore =
newest full + newest differential. The price showed up within four nights:
188 -> 351 -> 706 MB, because a file changed on Monday rode along again on
Tuesday, Wednesday and Thursday. That is exactly what msg 3533 had asked NOT
to happen, and the owner confirmed it when shown the numbers (Telegram msg
4139, 2026-09-26 11:03: "Csak a napi valtozas (ahogy kertem)").

So each nightly archive is now compared against the state recorded by the
PREVIOUS successful archive (full or incremental). The baseline file keeps the
full it descends from ("archive"), the incrementals taken since ("chain"), and
the per-file state as of the newest link ("files").

    restore = the newest full, then EVERY incremental after it, oldest first.

What this costs, stated rather than hidden: one missing or corrupt link makes
everything after it incomplete. Three things bound that risk: every archive is
verified against its manifest before the baseline advances; `due` demands a
FULL run as soon as any link of the chain is missing from --backup-dir; and
the weekly full (FULL_EVERY_DAYS in backup.sh) caps the chain at a week.

WHERE THE STATE COMES FROM: `filter` records it at filter time into a pending
file, and `advance` commits it only after the archive has been verified. A
file that changes between the filter and the copy lands in the archive newer
than the state says, so the next night takes it again -- the safe direction.
Snapshotting AFTER the copy would be the unsafe one: a change made during the
run would be recorded as saved without ever having been archived.

WHY NOT `tar --listed-incremental`: backup.sh does not tar the live tree. It
copies the selected paths into a fresh staging directory and tars that, so the
staged files get new inodes on every run and GNU tar's snapshot logic would
mark all of them as new. The comparison therefore has to happen on the LIVE
paths, before staging -- which is what this does. It also means the staging
copy itself shrinks from 3.3 GB to ~0.2 GB, so the saving is in wall clock and
disk churn too, not only in upload bytes.

WHAT AN INCREMENTAL CANNOT SAY: that a file was DELETED. Restoring a full plus
its incrementals brings back files removed in between. For a backup that is the
safe direction to err in, but it is a real property and not an oversight.

Subcommands:

  filter    read the path lists backup.sh built, expand directories to files,
            drop everything byte-identical to the baseline, write the trimmed
            lists back. Prints a one-line summary on stderr.

  snapshot  record the CURRENT state of those same lists as the new baseline.
            backup.sh calls this only after a FULL archive has passed
            verification -- a baseline written from an unverified archive would
            silently make the next incremental incomplete.

  advance   after an INCREMENTAL archive passed verification: make the state
            `filter --pending` recorded the new baseline and append the archive
            to the chain. The full it descends from and its age stay unchanged.

  due       exit 0 if the next run must be FULL (no baseline, unreadable
            baseline, one older than --max-age-days, or any archive of the
            chain missing), exit 1 if an incremental will do. Nothing is written.
"""
import argparse
import json
import os
import sys
import time


def expand(list_path, base):
    """Yield (relpath, abspath) for every FILE named by a path list.

    The lists mix files and directories (33 directories and 5252 files in the
    2026-09-22 manifest), because backup.sh hands whole trees like `.git` and
    `.claude/skills` to `cp -pR`. Expanding them here is what makes the
    comparison match what actually gets archived.
    """
    if not os.path.exists(list_path):
        return
    seen = set()
    with open(list_path, "r", encoding="utf-8", errors="surrogateescape") as fh:
        for line in fh:
            rel = line.rstrip("\n")
            if not rel:
                continue
            full = os.path.join(base, rel)
            if os.path.islink(full) or os.path.isfile(full):
                if rel not in seen:
                    seen.add(rel)
                    yield rel, full
            elif os.path.isdir(full):
                for dirpath, dirnames, filenames in os.walk(full, followlinks=False):
                    for name in filenames:
                        fp = os.path.join(dirpath, name)
                        r = os.path.relpath(fp, base)
                        if r not in seen:
                            seen.add(r)
                            yield r, fp


def state_of(path):
    """(mtime, size) or None. lstat, so a symlink is compared as a symlink."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return [int(st.st_mtime), int(st.st_size)]


def collect(groups):
    """{'repo/<rel>': [mtime, size]} over every group."""
    out = {}
    for group, list_path, base in groups:
        for rel, full in expand(list_path, base):
            s = state_of(full)
            if s is not None:
                out["%s/%s" % (group, rel)] = s
    return out


def load_baseline(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return None
    return data


def cmd_filter(args):
    base_data = load_baseline(args.baseline)
    if base_data is None:
        print("backup-delta: no usable baseline -- refusing to filter", file=sys.stderr)
        return 2
    known = base_data["files"]

    groups = [
        ("repo", args.repolist, args.repo_root, args.out_repo),
        ("home", args.homelist, args.home, args.out_home),
    ]
    kept = dropped = 0
    kept_bytes = dropped_bytes = 0
    pending = {}
    for group, list_path, base, out_path in groups:
        lines = []
        for rel, full in expand(list_path, base):
            cur = state_of(full)
            if cur is None:
                continue
            key = "%s/%s" % (group, rel)
            pending[key] = cur
            if known.get(key) == cur:
                dropped += 1
                dropped_bytes += cur[1]
                continue
            kept += 1
            kept_bytes += cur[1]
            lines.append(rel)
        with open(out_path, "w", encoding="utf-8", errors="surrogateescape") as fh:
            for rel in lines:
                fh.write(rel + "\n")

    if args.pending:
        write_json(args.pending, {"created_at": int(time.time()), "files": pending})

    chain = base_data.get("chain") or []
    mb = 1024.0 * 1024.0
    print(
        "backup-delta: incremental vs %s -- %d file(s) / %.1f MB changed, "
        "%d file(s) / %.1f MB unchanged and skipped"
        % (chain[-1] if chain else base_data.get("archive", "?"),
           kept, kept_bytes / mb, dropped, dropped_bytes / mb),
        file=sys.stderr,
    )
    return 0


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def cmd_advance(args):
    base_data = load_baseline(args.baseline)
    if base_data is None:
        print("backup-delta: no usable baseline -- nothing to advance", file=sys.stderr)
        return 2
    try:
        with open(args.pending, "r", encoding="utf-8") as fh:
            pending = json.load(fh)
    except (OSError, ValueError):
        pending = None
    if not isinstance(pending, dict) or not isinstance(pending.get("files"), dict):
        # Leaving the old state in place is safe: the next incremental simply
        # carries tonight's changes again.
        print("backup-delta: no usable pending state -- baseline NOT advanced", file=sys.stderr)
        return 2
    chain = list(base_data.get("chain") or [])
    chain.append(args.archive)
    base_data["chain"] = chain
    base_data["files"] = pending["files"]
    base_data["advanced_at"] = int(time.time())
    write_json(args.baseline, base_data)
    print("backup-delta: baseline advanced -- chain: %s + %d incremental(s), newest %s"
          % (base_data.get("archive"), len(chain), args.archive), file=sys.stderr)
    return 0


def cmd_snapshot(args):
    files = collect([
        ("repo", args.repolist, args.repo_root),
        ("home", args.homelist, args.home),
    ])
    tmp = args.baseline + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "created_at": int(time.time()),
                "stamp": args.stamp,
                "archive": args.archive,
                "files": files,
            },
            fh,
        )
    os.replace(tmp, args.baseline)
    total = sum(v[1] for v in files.values())
    print(
        "backup-delta: baseline written -- %d file(s), %.1f MB, base archive %s"
        % (len(files), total / 1024.0 / 1024.0, args.archive),
        file=sys.stderr,
    )
    return 0


def cmd_due(args):
    data = load_baseline(args.baseline)
    if data is None:
        print("backup-delta: FULL -- no usable baseline", file=sys.stderr)
        return 0
    archive = os.path.join(args.backup_dir, data.get("archive", ""))
    if not data.get("archive") or not os.path.exists(archive):
        # A baseline whose full archive has been pruned cannot be restored
        # from, so an incremental against it would be a promise, not a copy.
        print("backup-delta: FULL -- the base archive is gone (%s)" % data.get("archive"),
              file=sys.stderr)
        return 0
    for link in data.get("chain") or []:
        if not os.path.exists(os.path.join(args.backup_dir, link)):
            # A gap in the chain means everything after it restores incomplete.
            print("backup-delta: FULL -- a link of the chain is gone (%s)" % link, file=sys.stderr)
            return 0
    age_days = (time.time() - int(data.get("created_at", 0))) / 86400.0
    if age_days >= args.max_age_days:
        print("backup-delta: FULL -- baseline is %.1f days old (limit %.1f)"
              % (age_days, args.max_age_days), file=sys.stderr)
        return 0
    print("backup-delta: INCREMENTAL -- the full is %.1f days old, chain has %d link(s)"
          % (age_days, len(data.get("chain") or [])), file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--baseline", required=True)
        p.add_argument("--repo-root", required=True)
        p.add_argument("--home", required=True)
        p.add_argument("--repolist", required=True)
        p.add_argument("--homelist", required=True)

    p = sub.add_parser("filter")
    common(p)
    p.add_argument("--out-repo", required=True)
    p.add_argument("--out-home", required=True)
    p.add_argument("--pending", help="write the state seen now, for `advance` after verification")
    p.set_defaults(fn=cmd_filter)

    p = sub.add_parser("advance")
    p.add_argument("--baseline", required=True)
    p.add_argument("--pending", required=True)
    p.add_argument("--archive", required=True)
    p.set_defaults(fn=cmd_advance)

    p = sub.add_parser("snapshot")
    common(p)
    p.add_argument("--stamp", required=True)
    p.add_argument("--archive", required=True)
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("due")
    p.add_argument("--baseline", required=True)
    p.add_argument("--backup-dir", required=True)
    p.add_argument("--max-age-days", type=float, default=7.0)
    p.set_defaults(fn=cmd_due)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
