#!/usr/bin/env python3
"""scripts/upstream-sync.sh must not touch the working tree on a conflict (#721).

Before the fix a conflicting tick ran the real `git merge`, which wrote conflict
markers into the live checkout and took them out again with `git merge --abort`:
~1 s every 15 minutes in which gate scripts and web files were invalid. Now a
`git merge-tree --write-tree` dry run decides first.

Each case builds a throwaway repo with a bare "origin", copies the script into
it (BASE is derived from the script's own path) next to a recording stub of
agent-msg.sh, and runs it. Measured, not inferred: file bytes AND mtime, the
reflog (a merge --abort leaves "reset: moving to HEAD"), the state file, and the
number of notifications.

Run:  python3 scripts/__tests__/upstream-sync-merge-tree.test.py
"""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), 'upstream-sync.sh')
FAILS = []
ENV = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
ENV.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + (('  -- ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout


def write(path, text):
    with open(path, 'w') as fh:
        fh.write(text)


def repo(td, upstream_file, local_file='f', upstream=None):
    """Local main and origin/main diverged; upstream edits `upstream_file` (or runs
    `upstream(seed)` instead), local commits an edit to `local_file`."""
    seed = os.path.join(td, 'seed')
    os.makedirs(seed)
    git(seed, 'init', '-q', '-b', 'main')
    write(os.path.join(seed, 'f'), 'base\n')
    write(os.path.join(seed, 'h'), 'base\n')
    git(seed, 'add', '.')
    git(seed, 'commit', '-qm', 'base')
    git(td, 'clone', '-q', '--bare', seed, 'origin.git')
    work = os.path.join(td, 'work')
    git(td, 'clone', '-q', os.path.join(td, 'origin.git'), work)
    write(os.path.join(work, '.git', 'info', 'exclude'), 'scripts/\nstore/\n')
    if upstream:
        upstream(seed)
    else:
        write(os.path.join(seed, upstream_file), 'upstream\n')
    git(seed, 'commit', '-qam', 'upstream')
    git(seed, 'push', '-q', os.path.join(td, 'origin.git'), 'main')
    write(os.path.join(work, local_file), 'local\n')
    git(work, 'commit', '-qam', 'local')
    os.makedirs(os.path.join(work, 'scripts'))
    os.makedirs(os.path.join(work, 'store'))
    shutil.copy(SCRIPT, os.path.join(work, 'scripts', 'upstream-sync.sh'))
    write(os.path.join(work, 'scripts', 'agent-msg.sh'),
          'cat >> "$(dirname "$0")/../store/notify.log"; echo "---" >> "$(dirname "$0")/../store/notify.log"; echo "OK id=1"\n')
    return work


def run(work, env=None):
    return subprocess.run(['bash', os.path.join(work, 'scripts', 'upstream-sync.sh')], cwd=work,
                          env=env or ENV, capture_output=True, text=True, timeout=120)


def state(work):
    with open(os.path.join(work, 'store', 'upstream-sync.json')) as fh:
        return json.load(fh)


def notes(work):
    p = os.path.join(work, 'store', 'notify.log')
    return open(p).read().count('---\n') if os.path.exists(p) else 0


# --- 1. content conflict: the tree is not touched, one notification ----------
with tempfile.TemporaryDirectory() as td:
    work = repo(td, 'f')
    fpath = os.path.join(work, 'f')
    before = (open(fpath).read(), os.stat(fpath).st_mtime_ns, git(work, 'rev-parse', 'HEAD'))
    p = run(work)
    check('conflict: exit 0', p.returncode == 0, p.stderr)
    st = state(work)
    check('conflict: state result=conflict naming f', st['result'] == 'conflict' and 'f' in st['detail'].split(), st)
    after = (open(fpath).read(), os.stat(fpath).st_mtime_ns, git(work, 'rev-parse', 'HEAD'))
    check('conflict: f bytes, mtime and HEAD unchanged', before == after, (before, after))
    check('conflict: no merge was run (no abort in the reflog)', 'reset: moving to HEAD' not in git(work, 'reflog'))
    check('conflict: no MERGE_HEAD left', not os.path.exists(os.path.join(work, '.git', 'MERGE_HEAD')))
    check('conflict: one notification', notes(work) == 1, notes(work))
    p = run(work)
    check('conflict repeat: quiet (still one notification), state kept',
          notes(work) == 1 and state(work)['result'] == 'conflict' and 'unchanged' in p.stderr, (notes(work), p.stderr))

# --- 2. clean divergence: merged as before ------------------------------------
with tempfile.TemporaryDirectory() as td:
    work = repo(td, 'h')
    p = run(work)
    st = state(work)
    parents = git(work, 'rev-list', '--parents', '-n', '1', 'HEAD').split()
    check('clean: state merged, HEAD is a two-parent merge', st['result'] == 'merged' and len(parents) == 3, (st, parents))
    check('clean: upstream change present, local change kept',
          open(os.path.join(work, 'h')).read() == 'upstream\n' and open(os.path.join(work, 'f')).read() == 'local\n')
    check('clean: one notification', notes(work) == 1, notes(work))

# --- 3. merge-tree itself fails: no fallback to the real merge ----------------
with tempfile.TemporaryDirectory() as td:
    work = repo(td, 'f')
    shim = os.path.join(td, 'bin')
    os.makedirs(shim)
    real = shutil.which('git')
    write(os.path.join(shim, 'git'),
          '#!/bin/sh\nif [ "$1" = merge-tree ]; then echo "fatal: simulated" >&2; exit 128; fi\nexec %s "$@"\n' % real)
    os.chmod(os.path.join(shim, 'git'), 0o755)
    env = dict(ENV, PATH=shim + os.pathsep + ENV['PATH'])
    fpath = os.path.join(work, 'f')
    before = (open(fpath).read(), os.stat(fpath).st_mtime_ns)
    p = run(work, env)
    st = state(work)
    check('merge-tree error: state error with git message', st['result'] == 'error' and 'simulated' in st['detail'], st)
    check('merge-tree error: tree untouched, no merge run',
          before == (open(fpath).read(), os.stat(fpath).st_mtime_ns) and 'reset: moving to HEAD' not in git(work, 'reflog'))
    run(work, env)
    check('merge-tree error repeat: one notification', notes(work) == 1, notes(work))

def shim_git(td, script):
    """A git wrapper on PATH: `script` is sh run before the real git; REAL holds its path."""
    shim = os.path.join(td, 'bin')
    os.makedirs(shim, exist_ok=True)
    write(os.path.join(shim, 'git'), '#!/bin/sh\nREAL=%s\n%s\nexec "$REAL" "$@"\n' % (shutil.which('git'), script))
    os.chmod(os.path.join(shim, 'git'), 0o755)
    return dict(ENV, PATH=shim + os.pathsep + ENV['PATH'])


# --- 4. upstream renames a file that is dirty here (#726) ---------------------
# Rename detection listed only the new name, so the dirty old name slipped past
# the check, merge-tree said clean and the real merge refused, every 15 minutes.
def rename_f(seed):
    git(seed, 'mv', 'f', 'g')

with tempfile.TemporaryDirectory() as td:
    work = repo(td, None, local_file='h', upstream=rename_f)
    fpath = os.path.join(work, 'f')
    write(fpath, 'dirty\n')
    before = (open(fpath).read(), os.stat(fpath).st_mtime_ns, git(work, 'rev-parse', 'HEAD'))
    p = run(work)
    st = state(work)
    check('renamed dirty: state blocked naming f', st['result'] == 'blocked' and 'f' in st['detail'].split(), st)
    after = (open(fpath).read(), os.stat(fpath).st_mtime_ns, git(work, 'rev-parse', 'HEAD'))
    check('renamed dirty: f bytes, mtime and HEAD unchanged', before == after, (before, after))
    check('renamed dirty: no merge was run', 'reset: moving to HEAD' not in git(work, 'reflog')
          and not os.path.exists(os.path.join(work, '.git', 'MERGE_HEAD')))
    check('renamed dirty: one notification', notes(work) == 1, notes(work))
    run(work)
    check('renamed dirty repeat: quiet', notes(work) == 1 and state(work)['result'] == 'blocked', notes(work))

# --- 5. dirty file the merge does not touch: still merges ---------------------
with tempfile.TemporaryDirectory() as td:
    work = repo(td, None, local_file='h', upstream=rename_f)
    write(os.path.join(work, 'h'), 'dirty-unrelated\n')
    p = run(work)
    st = state(work)
    check('unrelated dirty: merged, dirty file kept', st['result'] == 'merged'
          and open(os.path.join(work, 'h')).read() == 'dirty-unrelated\n'
          and os.path.exists(os.path.join(work, 'g')), (st, p.stderr))

# --- 6. origin/main moves between the check and the merge (#726) -------------
# update.sh fetches on its own schedule; the commit that merge-tree checked must
# be the one that gets merged, not whatever origin/main points at a moment later.
with tempfile.TemporaryDirectory() as td:
    work = repo(td, 'h')
    git(work, 'fetch', '-q', 'origin', 'main')
    checked = git(work, 'rev-parse', 'origin/main').strip()
    later = git(work, 'commit-tree', '-p', checked, '-m', 'later', checked + '^{tree}').strip()
    env = shim_git(td, 'if [ "$1" = merge ]; then "$REAL" update-ref refs/remotes/origin/main %s; fi' % later)
    p = run(work, env)
    parents = git(work, 'rev-list', '--parents', '-n', '1', 'HEAD').split()
    check('moving origin: the checked commit is merged, not the later one',
          state(work)['result'] == 'merged' and len(parents) == 3 and parents[2] == checked,
          (state(work), parents, checked, later))

# --- 7. merge refused for a non-conflict reason: one notification -------------
with tempfile.TemporaryDirectory() as td:
    work = repo(td, 'h')
    env = shim_git(td, 'if [ "$1" = merge ] && [ "$2" = --no-ff ]; then echo "fatal: simulated refuse" >&2; exit 128; fi')
    p = run(work, env)
    st = state(work)
    check('merge refused: state error with git message', st['result'] == 'error' and 'simulated' in st['detail'], st)
    run(work, env)
    check('merge refused repeat: one notification', notes(work) == 1, notes(work))

# --- 8. upstream adds files that exist here UNTRACKED (#746) ------------------
# merge-tree cannot see the working tree, so it said clean; the real merge then
# refused ("Please move or remove them before you merge") and the error report
# lost the file names. Now the dry run names them and no merge runs.
def add_n_dx(seed):
    write(os.path.join(seed, 'n'), 'upstream\n')
    os.makedirs(os.path.join(seed, 'd'))
    write(os.path.join(seed, 'd', 'x'), 'upstream\n')
    git(seed, 'add', 'n', 'd/x')

with tempfile.TemporaryDirectory() as td:
    work = repo(td, None, local_file='h', upstream=add_n_dx)
    npath = os.path.join(work, 'n')
    os.makedirs(os.path.join(work, 'd'))
    write(npath, 'mine, untracked\n')
    write(os.path.join(work, 'd', 'x'), 'mine too\n')
    before = (open(npath).read(), os.stat(npath).st_mtime_ns, git(work, 'rev-parse', 'HEAD'))
    p = run(work)
    st = state(work)
    check('untracked clobber: exit 0', p.returncode == 0, p.stderr)
    check('untracked clobber: state blocked naming n and d/x',
          st['result'] == 'blocked' and {'n', 'd/x'} <= set(st['detail'].split()), st)
    after = (open(npath).read(), os.stat(npath).st_mtime_ns, git(work, 'rev-parse', 'HEAD'))
    check('untracked clobber: n bytes, mtime and HEAD unchanged', before == after, (before, after))
    check('untracked clobber: no merge was run', 'reset: moving to HEAD' not in git(work, 'reflog')
          and not os.path.exists(os.path.join(work, '.git', 'MERGE_HEAD')))
    note_text = open(os.path.join(work, 'store', 'notify.log')).read()
    check('untracked clobber: one notification naming the files',
          notes(work) == 1 and {'n', 'd/x'} <= set(note_text.split()), (notes(work), note_text))
    run(work)
    check('untracked clobber repeat: quiet', notes(work) == 1 and state(work)['result'] == 'blocked', notes(work))

# --- 9. upstream adds a file that exists here IGNORED (#746) ------------------
# The real merge OVERWRITES an ignored file without a word (measured 2026-11-02),
# so this one is data loss, not a refusal: it must block the same way.
def add_i(seed):
    write(os.path.join(seed, 'i'), 'upstream\n')
    git(seed, 'add', 'i')

with tempfile.TemporaryDirectory() as td:
    work = repo(td, None, local_file='h', upstream=add_i)
    write(os.path.join(work, '.git', 'info', 'exclude'), 'scripts/\nstore/\ni\n')
    ipath = os.path.join(work, 'i')
    write(ipath, 'mine, ignored\n')
    p = run(work)
    st = state(work)
    check('ignored clobber: state blocked naming i as ignored',
          st['result'] == 'blocked' and 'i (ignored)' in st['detail'], st)
    check('ignored clobber: i kept, no merge run',
          open(ipath).read() == 'mine, ignored\n' and 'reset: moving to HEAD' not in git(work, 'reflog')
          and len(git(work, 'rev-list', '--parents', '-n', '1', 'HEAD').split()) == 2, open(ipath).read())
    check('ignored clobber: one notification', notes(work) == 1, notes(work))
    run(work)
    check('ignored clobber repeat: quiet', notes(work) == 1, notes(work))

# --- 10. an unrelated untracked file: still merges, file kept ----------------
with tempfile.TemporaryDirectory() as td:
    work = repo(td, None, local_file='h', upstream=add_n_dx)
    write(os.path.join(work, 'u'), 'unrelated\n')
    p = run(work)
    st = state(work)
    check('unrelated untracked: merged, u kept, n and d/x created',
          st['result'] == 'merged' and open(os.path.join(work, 'u')).read() == 'unrelated\n'
          and open(os.path.join(work, 'n')).read() == 'upstream\n'
          and os.path.exists(os.path.join(work, 'd', 'x')), (st, p.stderr))

# --- 11. ignored FILE d here, upstream adds d/x (#746 review) ---------------
# Equal-name matching passed this: the real merge turned the ignored file into
# a directory without a word. A parent of an added path is a collision too.
def add_dx(seed):
    os.makedirs(os.path.join(seed, 'd'))
    write(os.path.join(seed, 'd', 'x'), 'upstream\n')
    git(seed, 'add', 'd/x')

with tempfile.TemporaryDirectory() as td:
    work = repo(td, None, local_file='h', upstream=add_dx)
    write(os.path.join(work, '.git', 'info', 'exclude'), 'scripts/\nstore/\nd\n')
    dpath = os.path.join(work, 'd')
    write(dpath, 'mine\n')
    p = run(work)
    st = state(work)
    check('ignored parent file: state blocked naming d as ignored',
          st['result'] == 'blocked' and 'd (ignored)' in st['detail'], (st, p.stderr))
    check('ignored parent file: d still a file with my bytes, no merge',
          os.path.isfile(dpath) and open(dpath).read() == 'mine\n'
          and len(git(work, 'rev-list', '--parents', '-n', '1', 'HEAD').split()) == 2, os.path.isfile(dpath))
    check('ignored parent file: one notification', notes(work) == 1, notes(work))

# --- 12. ignored d/y here, upstream adds a FILE d (#746 review) -------------
def add_d(seed):
    write(os.path.join(seed, 'd'), 'upstream\n')
    git(seed, 'add', 'd')

with tempfile.TemporaryDirectory() as td:
    work = repo(td, None, local_file='h', upstream=add_d)
    write(os.path.join(work, '.git', 'info', 'exclude'), 'scripts/\nstore/\nd/\n')
    os.makedirs(os.path.join(work, 'd'))
    ypath = os.path.join(work, 'd', 'y')
    write(ypath, 'mine\n')
    p = run(work)
    st = state(work)
    check('ignored child: state blocked naming d/y as ignored',
          st['result'] == 'blocked' and 'd/y (ignored)' in st['detail'], (st, p.stderr))
    check('ignored child: d/y kept, no merge',
          os.path.isfile(ypath) and open(ypath).read() == 'mine\n'
          and len(git(work, 'rev-list', '--parents', '-n', '1', 'HEAD').split()) == 2, os.path.isfile(ypath))
    check('ignored child: one notification', notes(work) == 1, notes(work))

# --- 13. UNTRACKED file d here, upstream adds d/x (#746 review) -------------
with tempfile.TemporaryDirectory() as td:
    work = repo(td, None, local_file='h', upstream=add_dx)
    dpath = os.path.join(work, 'd')
    write(dpath, 'mine\n')
    p = run(work)
    st = state(work)
    check('untracked parent file: state blocked naming d, not as ignored',
          st['result'] == 'blocked' and 'd' in st['detail'].split() and '(ignored)' not in st['detail'], (st, p.stderr))
    check('untracked parent file: d kept, no merge run',
          os.path.isfile(dpath) and open(dpath).read() == 'mine\n'
          and 'reset: moving to HEAD' not in git(work, 'reflog')
          and len(git(work, 'rev-list', '--parents', '-n', '1', 'HEAD').split()) == 2)
    check('untracked parent file: one notification', notes(work) == 1, notes(work))

# --- 14. a name with a space comes out whole ---------------------------------
def add_spaced(seed):
    write(os.path.join(seed, 'my file'), 'upstream\n')
    git(seed, 'add', 'my file')

with tempfile.TemporaryDirectory() as td:
    work = repo(td, None, local_file='h', upstream=add_spaced)
    write(os.path.join(work, 'my file'), 'mine\n')
    p = run(work)
    st = state(work)
    check('spaced name: blocked and named whole',
          st['result'] == 'blocked' and 'my file' in st['detail'], (st, p.stderr))

print()
if FAILS:
    print('%d FAILED: %s' % (len(FAILS), FAILS), file=sys.stderr)
    sys.exit(1)
print('All upstream-sync merge-tree tests passed.')
