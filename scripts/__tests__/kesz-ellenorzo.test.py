#!/usr/bin/env python3
"""Standalone integration tests for scripts/kesz-ellenorzo.py."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "kesz-ellenorzo.py"))
fails = 0


def check(name, condition):
    global fails
    print(("  ok   " if condition else "  FAIL ") + name)
    fails += 0 if condition else 1


def call(command, repo):
    return subprocess.run(command, cwd=repo, capture_output=True, text=True, check=False)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def repo():
    temp = tempfile.TemporaryDirectory()
    root = temp.name
    call(["git", "init", "-b", "main"], root)
    call(["git", "config", "user.name", "Test"], root)
    call(["git", "config", "user.email", "test@example.invalid"], root)
    write(os.path.join(root, "base.txt"), "base\n")
    call(["git", "add", "."], root)
    call(["git", "commit", "-m", "base"], root)
    call(["git", "checkout", "-b", "feat"], root)
    return temp, root


def run(root, *args, env=None):
    command = [sys.executable, TOOL, "--repo", root, *args]
    return subprocess.run(command, capture_output=True, text=True, env=env, timeout=15)


def commit(root, path, text):
    write(os.path.join(root, path), text)
    call(["git", "add", path], root)
    call(["git", "commit", "-m", path], root)


def fake(root, name, text):
    path = os.path.join(root, "node_modules", ".bin", name)
    write(path, text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def data(result):
    return json.loads(result.stdout)


with tempfile.TemporaryDirectory() as plain:
    result = subprocess.run([sys.executable, TOOL, "--repo", plain], capture_output=True, text=True)
    check("not a git directory", result.returncode == 2 and "git" in result.stderr.lower())

temp, root = repo()
result = run(root, "--base", "missing")
check("unknown base", result.returncode == 2 and "base" in result.stderr)
result = run(root)
check("empty diff", result.returncode == 2 and "Nincs valtozott fajl" in result.stdout)
temp.cleanup()

temp, root = repo()
commit(root, "scripts/hello.py", "print('hello')\n")
write(os.path.join(root, "scripts/__tests__/hello.test.py"), "print('  ok   one')\n")
result = run(root)
check("python passing test", result.returncode == 0 and "OK  scripts/__tests__/hello.test.py" in result.stdout and "1/0/0" in result.stdout)
temp.cleanup()

temp, root = repo()
commit(root, "scripts/brackets.py", "print('x')\n")
write(os.path.join(root, "scripts/__tests__/brackets.test.py"), "print('  [PASS] one')\nprint('  [PASS] two')\nprint('  [FAIL] three')\n")
item = next(r for r in data(run(root, "--json"))["rows"] if r["name"].endswith("brackets.test.py"))
check("bracketed PASS/FAIL counted", (item["passed"], item["failed"]) == (2, 1))
temp.cleanup()

temp, root = repo()
commit(root, "scripts/bad.py", "print('bad')\n")
write(os.path.join(root, "scripts/__tests__/bad.test.py"), "print('  FAIL detail from test')\nraise SystemExit(1)\n")
result = run(root)
check("python failing test tail", result.returncode == 1 and "FAIL  scripts/__tests__/bad.test.py" in result.stdout and "detail from test" in result.stdout)
temp.cleanup()

temp, root = repo()
commit(root, "scripts/lonely.py", "value = 1\n")
result = run(root)
check("python without test", result.returncode == 1 and "NINCS TESZT  NINCS TESZT scripts/lonely.py" in result.stdout)
temp.cleanup()

temp, root = repo()
commit(root, "scripts/muhely/jobs.py", "value = 1\n")
write(os.path.join(root, "scripts/muhely/__tests__/jobs_photo_test.py"), "print('  ok   photo')\n")
write(os.path.join(root, "scripts/__tests__/muhely-jobs-invoice.test.sh"), "echo 'ok invoice'\n")
commit(root, "scripts/voice/_vtools.py", "value = 2\n")
write(os.path.join(root, "scripts/voice/test_vtools_send.py"), "print('  ok   send')\n")
result = run(root, "--json")
names = {r["name"]: r["status"] for r in data(result)["rows"]}
check("subfolder test layouts found", result.returncode == 0
      and names.get("scripts/muhely/__tests__/jobs_photo_test.py") == "OK"
      and names.get("scripts/__tests__/muhely-jobs-invoice.test.sh") == "OK"
      and names.get("scripts/voice/test_vtools_send.py") == "OK"
      and not any(status == "NINCS TESZT" for status in names.values()))
temp.cleanup()

temp, root = repo()
fake(root, "vitest", "#!/usr/bin/env python3\nimport json, sys\n"
     "path = next(x.split('=', 1)[1] for x in sys.argv if x.startswith('--outputFile='))\n"
     "open('args.txt', 'w').write(' '.join(sys.argv[1:]))\n"
     "json.dump({'numPassedTests': 2, 'numFailedTests': 0, 'testResults': [{}]}, open(path, 'w'))\n")
write(os.path.join(root, "src/__tests__/gate-wiring.test.ts"), "const gate = 'scripts/hooks/my-gate.py'\n")
commit(root, "src/__tests__/gate-wiring.test.ts", "const gate = 'scripts/hooks/my-gate.py'\n")
call(["git", "checkout", "-q", "-b", "feat2"], root)
call(["git", "branch", "-q", "-f", "main", "feat2"], root)
commit(root, "scripts/hooks/my-gate.py", "value = 3\n")
result = run(root, "--json")
parsed = data(result)
args_file = os.path.join(root, "args.txt")
vitest_args = open(args_file, encoding="utf-8").read() if os.path.exists(args_file) else ""
check("hook named by a vitest test is not NINCS TESZT",
      result.returncode == 0 and "src/__tests__/gate-wiring.test.ts" in vitest_args
      and not any(r["status"] == "NINCS TESZT" for r in parsed["rows"])
      and "scripts/hooks/my-gate.py" not in parsed["uncovered"])
temp.cleanup()

temp, root = repo()
commit(root, "scripts/agent-briefing-build.py", "value = 4\n")
write(os.path.join(root, "scripts/__tests__/agent-briefing-stale.test.py"),
      "# runs scripts/agent-briefing-build.py\nprint('  ok   stale')\n")
result = run(root, "--json")
names = {r["name"]: r["status"] for r in data(result)["rows"]}
check("test that names the script is found", result.returncode == 0
      and names.get("scripts/__tests__/agent-briefing-stale.test.py") == "OK" and "NINCS TESZT" not in names.values())
temp.cleanup()

temp, root = repo()
commit(root, "jegyzet-á.txt", "x\n")
parsed = data(run(root, "--json"))
check("non-ASCII name is changed, not deleted", "jegyzet-á.txt" in parsed["changed"] and not parsed["deleted"])
temp.cleanup()

temp, root = repo()
commit(root, "scripts/tool.sh", "echo hi\n")
write(os.path.join(root, "scripts/__tests__/tool.test.sh"), "echo 'FAIL shell detail'\nexit 1\n")
result = run(root)
check("shell failing test", result.returncode == 1 and "FAIL  scripts/__tests__/tool.test.sh" in result.stdout
      and "shell detail" in result.stdout and "OK  bash -n scripts/tool.sh" in result.stdout)
temp.cleanup()

temp, root = repo()
write(os.path.join(root, "tsconfig.json"), "{}\n")
commit(root, "src/a.ts", "export const a: number = 1;\n")
result = run(root, "--json")
rows = data(result)["rows"]
check("missing tsc", result.returncode == 2 and any(r["name"] == "tsc" and r["status"] == "KIHAGYVA" for r in rows))
temp.cleanup()

temp, root = repo()
write(os.path.join(root, "tsconfig.json"), "{}\n")
fake(root, "tsc", "#!/bin/sh\necho 'a.ts(1): error TS2322: bad' >&2\necho 'b.ts(1): error TS2322: bad' >&2\nexit 2\n")
commit(root, "src/a.ts", "export const a: number = 'x';\n")
result = run(root, "--json")
tsc = next(r for r in data(result)["rows"] if r["name"] == "tsc")
check("tsc error count", result.returncode == 1 and tsc["status"] == "FAIL" and tsc["failed"] == 2)
temp.cleanup()

vitest = """#!/usr/bin/env python3
import json, os, sys, time
if os.environ.get('FAKE_SLEEP'): time.sleep(30)
path = next(x.split('=', 1)[1] for x in sys.argv if x.startswith('--outputFile='))
n = int(os.environ.get('FAKE_TESTS', '1'))
with open(path, 'w') as f:
    json.dump({'numPassedTests': 3 if n else 0, 'numFailedTests': 0, 'numPendingTests': 0,
               'numTodoTests': 0, 'testResults': [{}] if n else []}, f)
"""

temp, root = repo()
fake(root, "vitest", vitest)
commit(root, "src/a.test.ts", "test('a', () => {})\n")
result = run(root, "--json", env=dict(os.environ, FAKE_TESTS="1"))
vr = next(r for r in data(result)["rows"] if r["name"].startswith("vitest"))
check("vitest three passed", result.returncode == 0 and (vr["passed"], vr["failed"], vr["skipped"], vr["test_files"]) == (3, 0, 0, 1))
result = run(root, "--json", env=dict(os.environ, FAKE_TESTS="0"))
vr = next(r for r in data(result)["rows"] if r["name"].startswith("vitest"))
check("vitest zero is not green", result.returncode == 1 and vr["status"] == "NINCS TESZT")
started = time.monotonic()
result = run(root, "--json", "--timeout", "1", env=dict(os.environ, FAKE_SLEEP="1"))
elapsed = time.monotonic() - started
vr = next(r for r in data(result)["rows"] if r["name"].startswith("vitest"))
check("timeout kills process group", result.returncode == 1 and vr["status"] == "TIMEOUT" and elapsed < 8)
temp.cleanup()

temp, root = repo()
write(os.path.join(root, "store/.dashboard-token"), "token")
commit(root, "src/live.test.ts", "test('live', () => {})\n")
result = run(root, "--json")
parsed = data(result)
check("live install refuses vitest", result.returncode == 2 and parsed["header"]["live_install"] and any(r["status"] == "MEGTAGADVA" for r in parsed["rows"]))
text_result = run(root)
check("live header warning", "ELES CHECKOUT" in text_result.stdout)
temp.cleanup()

temp, root = repo()
write(os.path.join(root, "base.txt"), "dirty\n")
result = run(root, "--json")
parsed = data(result)
check("dirty file listed", not parsed["header"]["clean"] and any("base.txt" in x for x in parsed["header"]["dirty"]) and "base.txt" in parsed["changed"])
temp.cleanup()

temp, root = repo()
write(os.path.join(root, "new.txt"), "new\n")
os.unlink(os.path.join(root, "base.txt"))
result = run(root, "--json")
parsed = data(result)
check("untracked and deleted", "new.txt" in parsed["changed"] and "base.txt" in parsed["deleted"] and all("base.txt" not in r["files"] for r in parsed["rows"]))
temp.cleanup()

temp, root = repo()
for number in range(300):
    write(os.path.join(root, "many", f"{number:03}.txt"), "x\n")
result = run(root)
json_result = run(root, "--json")
check("big diff truncation and JSON", "tovabbi" in result.stdout and len(data(json_result)["changed"]) == 300)
temp.cleanup()

temp, root = repo()
write(os.path.join(root, "README.md"), "changed\n")
comment = os.path.join(root, "comment.txt")
result = run(root, "--json", "--komment", comment)
parsed = data(result)
expected = {"header", "changed", "deleted", "rows", "uncovered", "info", "counts", "exit_code"}
with open(comment, encoding="utf-8") as handle:
    comment_text = handle.read()
check("json schema and comment", expected == set(parsed) and "Mert:" in comment_text)
check("uncovered markdown", "README.md" in parsed["uncovered"])
text_result = run(root)
lower = text_result.stdout.lower()
check("no verdict wording", "minden rendben" not in lower and "kesz" not in lower)
temp.cleanup()

sys.exit(1 if fails else 0)
