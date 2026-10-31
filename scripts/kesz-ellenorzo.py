#!/usr/bin/env python3
"""Run checks selected by files changed on the current branch.

Usage: kesz-ellenorzo.py [--repo DIR] [--base REF] [--mind] [--build] [--e2e]
                         [--json] [--komment FILE] [--timeout SEC]
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATUSES = ("OK", "FAIL", "TIMEOUT", "NINCS TESZT", "KIHAGYVA", "MEGTAGADVA")
TS_TEST_RE = re.compile(r"^src/.*\.(?:test|spec)\.ts$|^src/.*\.test\.mts$")
SCRIPT_TEST_RE = re.compile(r"(?:^|/)(?:test_[^/]*\.py|[^/]*\.test\.(?:py|sh)|[^/]*_test\.(?:py|sh))$")


class UsageError(Exception):
    pass


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo")
    result.add_argument("--base", default="main")
    result.add_argument("--mind", action="store_true")
    result.add_argument("--build", action="store_true")
    result.add_argument("--e2e", action="store_true")
    result.add_argument("--json", action="store_true")
    result.add_argument("--komment")
    result.add_argument("--timeout", type=float, default=600)
    return result


def git(repo, *args, check=True):
    process = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
    if check and process.returncode:
        raise UsageError((process.stderr or process.stdout).strip())
    return process


def repository(path):
    if not shutil.which("git"):
        raise UsageError("git nincs a PATH-ban")
    cwd = os.path.abspath(path or os.getcwd())
    process = git(cwd, "rev-parse", "--show-toplevel", check=False)
    if process.returncode:
        raise UsageError("nem git work tree: " + cwd)
    return os.path.realpath(process.stdout.strip())


def changed_files(repo, base):
    # -z: without it git quotes non-ASCII names and they would look deleted
    groups = [
        git(repo, "diff", "-z", "--name-only", base + "..HEAD").stdout,
        git(repo, "diff", "-z", "--name-only", "HEAD").stdout,
        git(repo, "ls-files", "-z", "--others", "--exclude-standard").stdout,
    ]
    paths = {name for group in groups for name in group.split("\0") if name}
    return sorted(path for path in paths if not path.startswith(("node_modules/", "dist/")))


def row(name, status, command=None, exit_code=None, seconds=0, passed=None,
        failed=None, skipped=None, test_files=None, files=None, scope="", reason="", tail=None):
    return {"name": name, "status": status, "command": command or [], "exit": exit_code,
            "seconds": round(seconds, 3), "passed": passed, "failed": failed,
            "skipped": skipped, "test_files": test_files, "files": files or [],
            "scope": scope, "reason": reason, "tail": tail or []}


def command_text(command):
    return " ".join(str(item) for item in command)


def run_step(repo, name, command, timeout, files=None, scope="", parse=None):
    started = time.monotonic()
    try:
        process = subprocess.Popen(command, cwd=repo, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                   errors="replace", start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            status = "OK" if process.returncode == 0 else "FAIL"
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            status = "TIMEOUT"
    except OSError as exc:
        return row(name, "KIHAGYVA", command, None, time.monotonic() - started,
                   files=files, scope=scope, reason=str(exc))
    seconds = time.monotonic() - started
    counts = (None, None, None, None)
    if parse:
        counts = parse(stdout, stderr)
    output = stderr if stderr.strip() else stdout
    result = row(name, status, command, process.returncode, seconds, *counts,
                 files=files, scope=scope)
    if status in ("FAIL", "TIMEOUT"):
        result["tail"] = output.splitlines()[-20:]
    return result


def missing_tool(name, files=None):
    reason = f"nincs node_modules/.bin/{name} (worktree-ben: ln -s <fo checkout>/node_modules node_modules)"
    return row(name, "KIHAGYVA", files=files, reason=reason)


def path_tool(repo, name):
    path = os.path.join(repo, "node_modules", ".bin", name)
    return path if os.path.isfile(path) else None


def parse_python(stdout, stderr):
    text = stdout + "\n" + stderr
    match = re.search(r"Ran (\d+) tests?", text)
    if match:
        total = int(match.group(1))
        failed = errors = skipped = 0
        bad = re.search(r"FAILED \(([^)]*)\)", text)
        good = re.search(r"OK(?: \(([^)]*)\))?", text)
        details = (bad.group(1) if bad else (good.group(1) if good and good.group(1) else ""))
        for key, value in re.findall(r"(failures|errors|skipped)=(\d+)", details):
            if key == "failures": failed = int(value)
            elif key == "errors": errors = int(value)
            else: skipped = int(value)
        return total - failed - errors - skipped, failed + errors, skipped, None
    passed = len(re.findall(r"(?mi)^\s*(?:ok|PASS|\[(?:ok|PASS)\])(?:\s|:|$)", text))
    failed = len(re.findall(r"(?mi)^\s*(?:FAIL|\[FAIL\])(?:\s|:|$)", text))
    return (passed, failed, 0, None) if passed or failed else (None, None, None, None)


def parse_shell(stdout, stderr):
    text = stdout + "\n" + stderr
    passed = len(re.findall(r"(?mi)^\s*(?:ok|PASS|\[(?:ok|PASS)\])(?:\s|:|$)", text))
    failed = len(re.findall(r"(?mi)^\s*(?:FAIL|not ok|\[FAIL\])(?:\s|:|$)", text))
    return (passed, failed, 0, None) if passed or failed else (None, None, None, None)


def parse_playwright(stdout, stderr):
    text = stdout + "\n" + stderr
    values = {}
    for number, label in re.findall(r"(\d+)\s+(passed|failed|skipped)", text):
        values[label] = int(number)
    return values.get("passed"), values.get("failed"), values.get("skipped"), None


def vitest_step(repo, name, command, timeout, files, source_names=None, arguments=None):
    report = tempfile.NamedTemporaryFile(prefix="kesz-vitest-", suffix=".json", delete=False)
    report.close()
    os.unlink(report.name)
    command = command + ["--reporter=json", "--outputFile=" + report.name] + (arguments or [])

    def parse_report(_stdout, _stderr):
        try:
            with open(report.name, encoding="utf-8") as handle:
                data = json.load(handle)
            return (data.get("numPassedTests"), data.get("numFailedTests"),
                    (data.get("numPendingTests") or 0) + (data.get("numTodoTests") or 0),
                    len(data.get("testResults") or []))
        except (OSError, ValueError, TypeError):
            return None, None, None, None

    result = run_step(repo, name, command, timeout, files, parse=parse_report)
    try:
        os.unlink(report.name)
    except OSError:
        pass
    if result["status"] != "TIMEOUT" and result["test_files"] == 0:
        result["status"] = "NINCS TESZT"
        result["reason"] = "0 tesztfajl; forrasok: " + ", ".join(source_names or files)
    return result


def script_tests(repo, path):
    # The repo's own naming habits: scripts/__tests__/NAME*.test.{py,sh} (subfolder scripts
    # carry the folder as prefix, muhely-NAME-*), DIR/__tests__/NAME*_test.{py,sh}, DIR/test_NAME*.py.
    folder, name = os.path.split(path)
    stem = os.path.splitext(name)[0].lstrip("_")
    sub = os.path.relpath(folder, "scripts") if folder.startswith("scripts/") else ""
    patterns = []
    for item in {stem, stem.replace("-", "_"), stem.replace("_", "-")}:
        for base in [item] + ([sub.replace(os.sep, "-") + "-" + item] if sub else []):
            for ext in ("py", "sh"):
                patterns += [f"scripts/__tests__/{base}.test.{ext}", f"scripts/__tests__/{base}-*.test.{ext}",
                             f"scripts/__tests__/{base}_*.test.{ext}"]
        for ext in ("py", "sh"):
            patterns += [os.path.join(folder, "__tests__", f"{item}{tail}.{ext}")
                         for tail in ("_test", "_*_test", ".test")]
        patterns += [os.path.join(folder, f"test_{item}.py"), os.path.join(folder, f"test_{item}_*.py")]
    return sorted({os.path.relpath(hit, repo) for pattern in patterns
                   for hit in glob.glob(os.path.join(repo, pattern))})


def named_in_tests(repo, path):
    found = git(repo, "grep", "--untracked", "-l", "-F", os.path.basename(path), check=False).stdout.splitlines()
    return ([item for item in found if SCRIPT_TEST_RE.search(item) and item != path],
            [item for item in found if TS_TEST_RE.match(item)])


def info_for(repo, changed, deleted):
    sizes = []
    caps = []
    for path in changed:
        if path in deleted:
            continue
        try:
            with open(os.path.join(repo, path), encoding="utf-8") as handle:
                lines = sum(1 for _ in handle)
        except (OSError, UnicodeError):
            continue
        sizes.append((path, lines))
        if os.path.basename(path) in ("CLAUDE.md", "SKILL.md") and lines > 500:
            caps.append((path, lines))
    return {"largest": [list(item) for item in sorted(sizes, key=lambda x: (-x[1], x[0]))[:5]],
            "line_cap": [list(item) for item in sorted(caps)]}


def package_has_script(repo, name):
    try:
        with open(os.path.join(repo, "package.json"), encoding="utf-8") as handle:
            return name in (json.load(handle).get("scripts") or {})
    except (OSError, ValueError):
        return False


def collect(args, repo, header, changed, deleted):
    rows = []
    covered = set()
    existing = [path for path in changed if path not in deleted]
    ts_config = any(re.search(r"(?:^|/)tsconfig[^/]*\.json$", p) for p in existing)
    ts_files = [p for p in existing if p.endswith((".ts", ".mts", ".cts", ".tsx"))]
    if args.mind or ((ts_files or ts_config) and os.path.isfile(os.path.join(repo, "tsconfig.json"))):
        covered.update(ts_files)
        tool = path_tool(repo, "tsc")
        if not tool:
            rows.append(missing_tool("tsc", ts_files))
        else:
            result = run_step(repo, "tsc", [tool, "--noEmit", "-p", "tsconfig.json"], args.timeout,
                              ts_files, "teljes projekt (tsconfig.json)",
                              lambda out, err: (None, len(re.findall(r"error TS\d+", out + err)), None, None))
            rows.append(result)

    script_files = [p for p in existing if p.endswith((".py", ".sh")) and not SCRIPT_TEST_RE.search(p)]
    changed_script_tests = [p for p in existing if SCRIPT_TEST_RE.search(p)]
    # a test that names the script counts too (agent-briefing-*.test.py, vitest hook tests)
    named = {path: named_in_tests(repo, path) for path in script_files}
    referenced = {path: refs[1] for path, refs in named.items()}
    ts_tests = [p for p in existing if TS_TEST_RE.match(p)]
    sources = [p for p in existing if p.endswith((".ts", ".mts", ".tsx", ".js", ".mjs", ".cjs"))
               and p not in ts_tests]
    vitest_tests = sorted(set(ts_tests).union(*referenced.values()))
    if args.mind or vitest_tests or sources:
        vitest_files = ts_tests + sources + [path for path, refs in referenced.items() if refs]
        covered.update(vitest_files)
        vitest = path_tool(repo, "vitest")
        if header["live_install"]:
            reason = "eles checkout, a suite itt megtagadja a futast (src/__tests__/setup/assert-not-live-install.ts); futtasd worktree-bol"
            rows.append(row("vitest", "MEGTAGADVA", files=vitest_files, reason=reason))
        elif not vitest:
            rows.append(missing_tool("vitest", vitest_files))
        elif args.mind:
            rows.append(vitest_step(repo, "vitest teljes", [vitest, "run"], args.timeout, vitest_files))
        else:
            if vitest_tests:
                rows.append(vitest_step(repo, "vitest tesztek", [vitest, "run"], args.timeout,
                                        vitest_tests, arguments=vitest_tests))
            if sources:
                result = vitest_step(repo, "vitest related",
                                     [vitest, "related", "--run", "--passWithNoTests"],
                                     args.timeout, sources, sources, sources)
                result["reason"] = result["reason"] or "halmaz-szintu: nem mondja meg, melyik forrashoz volt teszt"
                rows.append(result)

    python, bash = shutil.which("python3"), shutil.which("bash")
    ran_tests = set()

    def run_test(test, files):
        if test in ran_tests:
            return
        ran_tests.add(test)
        tool, label, parse = ((python, "python3", parse_python) if test.endswith(".py")
                              else (bash, "bash", parse_shell))
        rows.append(run_step(repo, test, [tool, test], args.timeout, files, parse=parse) if tool
                    else row(test, "KIHAGYVA", files=files, reason=label + " nincs a PATH-ban"))

    for path in script_files:
        covered.add(path)
        if path.endswith(".py"):
            name, tool, label, command = "py_compile " + path, python, "python3", [python, "-m", "py_compile", path]
        else:
            name, tool, label, command = "bash -n " + path, bash, "bash", [bash, "-n", path]
        rows.append(run_step(repo, name, command, args.timeout, [path]) if tool
                    else row(name, "KIHAGYVA", files=[path], reason=label + " nincs a PATH-ban"))
        tests = sorted(set(script_tests(repo, path)) | set(named[path][0]))
        if not tests and not referenced[path]:
            rows.append(row("NINCS TESZT " + path, "NINCS TESZT", files=[path],
                            reason="nincs teszt a szokasos helyeken, es vitest-teszt sem nevezi meg"))
        for test in tests:
            run_test(test, [path, test])
    for test in changed_script_tests:
        covered.add(test)
        run_test(test, [test])

    node = shutil.which("node")
    for path in [p for p in existing if p.endswith((".js", ".mjs", ".cjs")) and not p.startswith("src/")]:
        covered.add(path)
        rows.append(run_step(repo, "node --check " + path, [node, "--check", path], args.timeout, [path])
                    if node else row("node --check " + path, "KIHAGYVA", files=[path], reason="node nincs a PATH-ban"))

    rows.append(run_step(repo, "git diff --check", ["git", "diff", "--check", header["base"]], args.timeout))
    if args.mind and package_has_script(repo, "syntax-check"):
        npm = shutil.which("npm")
        rows.append(run_step(repo, "npm run syntax-check", [npm, "run", "syntax-check"], args.timeout)
                    if npm else row("npm run syntax-check", "KIHAGYVA", reason="npm nincs a PATH-ban"))
    if args.build:
        if header["live_install"]:
            rows.append(row("npm run build", "MEGTAGADVA", reason="a build az eles fa dist/ mappajat irja"))
        else:
            npm = shutil.which("npm")
            rows.append(run_step(repo, "npm run build", [npm, "run", "build"], args.timeout)
                        if npm else row("npm run build", "KIHAGYVA", reason="npm nincs a PATH-ban"))
    if args.e2e:
        tool = path_tool(repo, "playwright")
        rows.append(run_step(repo, "playwright e2e", [tool, "test", "--config=playwright.config.ts"],
                             args.timeout, parse=parse_playwright) if tool else missing_tool("playwright"))
    return rows, sorted(set(existing) - covered)


def counts_line(counts):
    return "Sorok: " + ", ".join(f"{counts[name]} {name}" for name in STATUSES)


def print_text(result):
    h = result["header"]
    print(f"Ag: {h['branch']}\nHEAD: {h['head']}\nBase: {h['base_ref']} {h['base']}")
    print("Fa tiszta: " + ("igen" if h["clean"] else "nem"))
    for dirty in h["dirty"]:
        print("  " + dirty)
    print("Worktree: " + ("igen" if h["worktree"] else "nem"))
    print("Live install: " + ("igen" if h["live_install"] else "nem"))
    if h["live_install"]:
        print("ELES CHECKOUT: a vitest es a build itt nem futtathato, futtasd worktree-bol")
    if not result["changed"]:
        print("Nincs valtozott fajl a base ota")
    else:
        present = [path for path in result["changed"] if path not in result["deleted"]]
        shown = (present + result["deleted"])[:40]
        print("Valtozott fajlok:")
        for path in shown:
            if path not in result["deleted"]:
                print("  " + path)
        deleted_shown = [path for path in shown if path in result["deleted"]]
        if deleted_shown:
            print("Torolve:")
            for path in deleted_shown:
                print("  " + path)
        if len(result["changed"]) > 40:
            print(f"  ... es {len(result['changed']) - 40} tovabbi")
    for item in result["rows"]:
        numbers = "/".join("-" if item[key] is None else str(item[key]) for key in ("passed", "failed", "skipped"))
        print(f"{item['status']}  {item['name']}  exit={item['exit'] if item['exit'] is not None else '-'}  "
              f"{item['seconds']:.1f}s  {numbers}  $ {command_text(item['command'])}")
        if item["reason"]:
            print("    " + item["reason"])
        for line in item["tail"]:
            print("    " + line)
    if result["info"]["largest"]:
        print("Legnagyobb valtozott szovegfajlok: " + ", ".join(f"{p} ({n})" for p, n in result["info"]["largest"]))
    for path, lines in result["info"]["line_cap"]:
        print(f"line-cap jelolt: {path} ({lines})")
    print("Lefedetlen: " + (", ".join(result["uncovered"]) if result["uncovered"] else "nincs"))
    print(counts_line(result["counts"]))


def write_comment(path, result):
    h = result["header"]
    now = dt.datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d %H:%M")
    tree = "tiszta" if h["clean"] else f"{len(h['dirty'])} piszkos fajl"
    place = "worktree" if h["worktree"] else "ELES CHECKOUT"
    lines = [f"Kesz-ellenorzo (scripts/kesz-ellenorzo.py), {now} Europe/London",
             f"Mert: ag {h['branch']}, HEAD {h['head'][:8]}, base {h['base'][:8]} ({h['base_ref']}), fa {tree}, {place}"]
    for item in result["rows"]:
        numbers = "/".join("-" if item[k] is None else str(item[k]) for k in ("passed", "failed", "skipped"))
        lines.append(f"{item['status']} {item['name']} {numbers} {item['seconds']:.1f}s")
    lines += ["Lefedetlen: " + (", ".join(result["uncovered"]) if result["uncovered"] else "nincs"),
              counts_line(result["counts"])]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv=None):
    args = parser().parse_args(argv)
    if args.timeout <= 0:
        print("--timeout pozitiv szam legyen", file=sys.stderr)
        return 2
    try:
        repo = repository(args.repo)
        probe = git(repo, "rev-parse", "--verify", args.base + "^{commit}", check=False)
        if probe.returncode:
            raise UsageError("ismeretlen base ref: " + args.base)
        base = git(repo, "merge-base", "HEAD", args.base).stdout.strip()
        branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        dirty = git(repo, "status", "--porcelain").stdout.splitlines()
        git_dir = git(repo, "rev-parse", "--git-dir").stdout.strip()
        common = git(repo, "rev-parse", "--git-common-dir").stdout.strip()
        absolute = lambda p: os.path.realpath(p if os.path.isabs(p) else os.path.join(repo, p))
        live = any(os.path.exists(os.path.join(repo, path)) for path in
                   ("store/.dashboard-token", "store/claudeclaw.db", "store/.claude-oauth-token"))
        header = {"repo": repo, "branch": branch, "head": head, "base_ref": args.base,
                  "base": base, "clean": not dirty, "dirty": dirty,
                  "worktree": absolute(git_dir) != absolute(common), "live_install": live}
        changed = changed_files(repo, base)
    except (UsageError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    deleted = [path for path in changed if not os.path.lexists(os.path.join(repo, path))]
    rows, uncovered = collect(args, repo, header, changed, deleted) if changed else ([], [])
    counts = {status: sum(item["status"] == status for item in rows) for status in STATUSES}
    if counts["FAIL"] or counts["TIMEOUT"] or counts["NINCS TESZT"]:
        exit_code = 1
    elif counts["KIHAGYVA"] or counts["MEGTAGADVA"] or not rows:
        exit_code = 2
    else:
        exit_code = 0
    result = {"header": header, "changed": changed, "deleted": deleted, "rows": rows,
              "uncovered": uncovered, "info": info_for(repo, changed, deleted),
              "counts": counts, "exit_code": exit_code}
    if args.komment:
        write_comment(args.komment, result)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print_text(result)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
