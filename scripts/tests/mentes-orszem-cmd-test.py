import os, shutil, subprocess, sys, time, json, tempfile
# Fault tests for scripts/mentes-orszem-cmd.py (#648): a fake MARVEEN_ROOT with stub
# backup-due/backup-coverage/agent-msg/google_auth, one temp dir per case.
SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mentes-orszem-cmd.py')
BASE = tempfile.mkdtemp(prefix='mentes-orszem-test-')
NOW = int(time.time())

def mk(state=str(NOW - 13 * 3600), due='NEM', cov=None, drive=None, msg='OK id=7', backups=('a',)):
    r = tempfile.mkdtemp(dir=BASE)
    for d in ('store', 'scripts/muhely', 'backups'):
        os.makedirs(os.path.join(r, d))
    if state is not None:
        open(os.path.join(r, 'store/.last-backup-ok'), 'w').write(state)
    open(os.path.join(r, 'scripts/backup-due.py'), 'w').write(f'print({due!r})\n')
    cov = cov if cov is not None else json.dumps({'ok': True, 'manifest_age_days': 0.5, 'repo': [], 'home': []})
    open(os.path.join(r, 'scripts/backup-coverage.py'), 'w').write(f'print({cov!r})\n')
    open(os.path.join(r, 'scripts/agent-msg.sh'), 'w').write(f'echo "$3" > "{r}/sent.txt"; echo {msg!r}\n')
    for i, b in enumerate(backups):
        p = os.path.join(r, f'backups/claudeclaw-2026102{i}-200000-diff.tar.gz')
        open(p, 'w').write(b)
        open(p + '.gpg', 'w').write(b * 3)
    newest = f'claudeclaw-2026102{len(backups)-1}-200000-diff.tar.gz.gpg'
    drive = drive if drive is not None else f'return {{"files":[{{"name":"{newest}","size":"{len(backups[-1])*3}"}}]}}'
    open(os.path.join(r, 'scripts/muhely/google_auth.py'), 'w').write(f'def call(*a, **k):\n    {drive}\n')
    return r

def run(r, *args):
    p = subprocess.run([sys.executable, '-I', SCRIPT, *args], env={**os.environ, 'MARVEEN_ROOT': r},
                       capture_output=True, text=True, timeout=60)
    return p.returncode, p.stderr.strip()

cases = []
def case(name, want_exit, want_in, r, *args):
    code, err = run(r, *args)
    ok = code == want_exit and want_in in err
    cases.append(ok)
    print('PASS' if ok else 'FAIL', name, code, err[:110])
    return r

case('happy', 0, '', mk())
case('state missing', 1, 'hianyzik', mk(state=None))
case('state empty', 1, 'hianyzik', mk(state=''))
case('state junk', 1, 'hianyzik', mk(state='abc'))
case('state future', 1, 'jovobeli', mk(state=str(NOW + 3600)))
case('state ms stamp', 1, 'jovobeli', mk(state=str(NOW * 1000)))
case('8 days old', 1, '8.0 napja', mk(state=str(NOW - 8 * 86400)))
case('due + 30h', 1, 'elbukott', mk(state=str(NOW - 30 * 3600), due='ESEDEKES'))
case('due + 13h ok', 0, '', mk(due='ESEDEKES'))
case('not due + 30h ok', 0, '', mk(state=str(NOW - 30 * 3600)))
r = mk(); os.remove(os.path.join(r, 'backups/claudeclaw-20261020-200000-diff.tar.gz.gpg'))
open(os.path.join(r, 'backups/claudeclaw-20261021-200000-diff.tar.gz.gpg'), 'w').write('aaa')
case('orphan tar.gz', 1, 'felbeszakadt', r, '--no-drive')
r = mk(); old = os.path.join(r, 'backups/claudeclaw-20261020-200000-diff.tar.gz')
os.remove(old + '.gpg'); os.utime(old, (NOW - 72 * 3600, NOW - 72 * 3600))
case('old orphan ignored', 0, '', r, '--no-drive')
case("no local gpg", 1, "nincs helyi", mk(backups=(), drive="return {}"))
case('drive other name', 1, 'nem a helyi', mk(drive='return {"files":[{"name":"claudeclaw-x.gpg","size":"3"}]}'))
case('drive size differs', 1, 'merete elter', mk(drive='return {"files":[{"name":"claudeclaw-20261020-200000-diff.tar.gz.gpg","size":"4"}]}'))
case('drive empty', 1, 'nincs egy', mk(drive='return {"files":[]}'))
case('drive network err', 1, 'OSError', mk(drive='raise OSError("x")'))
case('drive auth exit', 1, 'nem erte el', mk(drive='raise SystemExit("token expired")'))
case('cov not json', 1, 'nem futott', mk(cov='boom'))
case('cov huge garbage', 1, 'nem futott', mk(cov='x' * 200000))
case('manifest old', 1, 'manifeszt', mk(cov=json.dumps({'manifest_age_days': 9, 'repo': []})))
case('manifest none', 1, 'manifeszt', mk(cov=json.dumps({'manifest_age_days': None, 'repo': []})))
gap = json.dumps({'manifest_age_days': 1, 'repo': [{'dir': 'newdir', 'uncovered': 3}]})
r = case('new gap wakes', 0, '', mk(cov=gap))
woke = os.path.exists(os.path.join(r, 'sent.txt')) and 'repo/newdir' in open(os.path.join(r, 'sent.txt')).read()
cases.append(woke); print('PASS' if woke else 'FAIL', 'wake text has repo/newdir')
os.remove(os.path.join(r, 'sent.txt'))
case('same gap silent', 0, '', r)
silent = not os.path.exists(os.path.join(r, 'sent.txt')); cases.append(silent); print('PASS' if silent else 'FAIL', 'no second wake')
r = case('wake send fails', 1, 'nem ment ki', mk(cov=gap, msg='FAIL'))
case('wake fail retried next day', 1, 'nem ment ki', r)
print(f'{sum(cases)}/{len(cases)}')
shutil.rmtree(BASE)
sys.exit(0 if all(cases) else 1)
