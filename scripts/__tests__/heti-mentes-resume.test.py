#!/usr/bin/env python3
"""Hermetic tests for the interrupted-run marker in scripts/heti-mentes-cmd.py (#765).

A due run writes store/.backup-running and removes it when it ends, failed or
not, while holding a flock on store/.backup-run.lock. A marker with a free lock
means the run was killed, and --resume-interrupted starts it again, once.

No backup runs: backup-due.py and the backup body are stubbed, and RUNNING,
RUN_LOCK and LOG point into a temp dir.
"""
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "heti-mentes-cmd.py")


def load():
    spec = importlib.util.spec_from_file_location("heti_mentes_cmd", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Due:
    def __init__(self, code, stdout="ESEDEKES\n"):
        self.returncode, self.stdout, self.stderr = code, stdout, ""


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = load()
        self.m.RUNNING = os.path.join(self.tmp.name, ".backup-running")
        self.m.RUN_LOCK = os.path.join(self.tmp.name, ".backup-run.lock")
        self.m.LOG = os.path.join(self.tmp.name, "log")
        self.due_code = 0
        self.body_calls = []
        self.calls = []
        self.send_out = (0, "OK id=1\n")

        def fake_run(cmd, *a, **k):
            self.calls.append(cmd)
            if "scripts/agent-msg.sh" in cmd:
                return Due(*self.send_out)
            return Due(self.due_code)
        # The module's `subprocess` is the global one: patch and restore it.
        self.real_run = subprocess.run
        self.addCleanup(setattr, subprocess, "run", self.real_run)
        subprocess.run = fake_run
        self.addCleanup(setattr, sys, "argv", list(sys.argv))

        def body():
            # The marker must exist and the lock must be held while the body runs.
            self.body_calls.append((self.m.read_marker(), self.lock_is_held()))
            return 0
        self.m.run_backup = body

    def tearDown(self):
        self.tmp.cleanup()

    def lock_is_held(self):
        fd = os.open(self.m.RUN_LOCK, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
        finally:
            os.close(fd)

    def hold_lock(self, release_after=None):
        fd = os.open(self.m.RUN_LOCK, os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        if release_after is None:
            self.addCleanup(os.close, fd)
            return
        t = threading.Timer(release_after, self.release, (fd,))
        t.start()
        self.addCleanup(t.join)

    def release(self, fd):
        self.released = True
        self.on_release()
        os.close(fd)

    def on_release(self):
        pass

    def sent(self):
        return [c for c in self.calls if "scripts/agent-msg.sh" in c]

    def log_text(self):
        try:
            with open(self.m.LOG) as fh:
                return fh.read()
        except FileNotFoundError:
            return ""

    def write_marker(self, attempt=1):
        with open(self.m.RUNNING, "w") as fh:
            json.dump({"pid": 4242, "started": "2026-11-01 20:00:11", "attempt": attempt}, fh)

    def test_no_marker_is_silent(self):
        self.assertEqual(self.m.resume_interrupted(), 0)
        self.assertEqual(self.body_calls, [])
        self.assertFalse(os.path.exists(self.m.LOG))

    def test_due_run_holds_marker_and_lock_then_clears(self):
        self.assertEqual(self.m.nightly(), 0)
        marker, held = self.body_calls[0]
        self.assertEqual(marker["attempt"], 1)
        self.assertTrue(held)
        self.assertIsNone(self.m.read_marker())
        self.assertFalse(self.lock_is_held())

    def test_failed_run_clears_marker(self):
        def boom():
            raise self.m.Fail("x")
        self.m.run_backup = boom
        with self.assertRaises(self.m.Fail):
            self.m.nightly()
        self.assertIsNone(self.m.read_marker())
        self.assertFalse(self.lock_is_held())

    def test_not_due_writes_no_marker(self):
        self.due_code = 1
        self.assertEqual(self.m.nightly(), 0)
        self.assertIsNone(self.m.read_marker())

    def test_marker_with_free_lock_reruns_as_attempt_2(self):
        self.write_marker(attempt=1)
        self.assertEqual(self.m.resume_interrupted(), 0)
        self.assertEqual(len(self.body_calls), 1)
        self.assertEqual(self.body_calls[0][0]["attempt"], 2)
        self.assertIsNone(self.m.read_marker())

    def test_marker_with_held_lock_is_left_alone(self):
        self.write_marker()
        self.hold_lock()
        self.assertEqual(self.m.resume_interrupted(), 0)
        self.assertEqual(self.body_calls, [])
        self.assertIsNotNone(self.m.read_marker())

    def test_regular_run_waits_for_a_resume_that_then_succeeds(self):
        # An exit 0 while the resume still runs would read as "recovered".
        self.write_marker(attempt=2)
        self.released = False

        def resume_ok():
            os.remove(self.m.RUNNING)
            self.due_code = 1  # the resume left a good copy
        self.on_release = resume_ok
        self.hold_lock(release_after=0.3)
        self.assertEqual(self.m.nightly(), 0)
        self.assertTrue(self.released)  # it returned only after the lock was free
        self.assertEqual(self.body_calls, [])
        self.assertIsNone(self.m.read_marker())

    def test_regular_run_does_the_backup_after_a_failed_resume(self):
        self.write_marker(attempt=2)
        self.released = False
        self.on_release = lambda: os.remove(self.m.RUNNING)  # failed, still due
        self.hold_lock(release_after=0.3)
        self.assertEqual(self.m.nightly(), 0)
        self.assertTrue(self.released)
        self.assertEqual(len(self.body_calls), 1)
        self.assertEqual(self.body_calls[0][0]["attempt"], 1)

    def test_attempt_cap_fails_and_clears(self):
        self.write_marker(attempt=self.m.RESUME_MAX_ATTEMPTS)
        with self.assertRaises(self.m.Fail):
            self.m.resume_interrupted()
        self.assertEqual(self.body_calls, [])
        self.assertIsNone(self.m.read_marker())
        self.assertFalse(self.lock_is_held())

    def test_resume_when_no_longer_due_clears_stale_marker(self):
        self.due_code = 1
        self.write_marker()
        self.assertEqual(self.m.resume_interrupted(), 0)
        self.assertEqual(self.body_calls, [])
        self.assertIsNone(self.m.read_marker())

    def test_attempt_is_counted_before_the_due_check(self):
        # A kill between the resume's decision and the body must not be forgotten.
        self.write_marker(attempt=1)
        seen = []

        def fake_locked_run(attempt):
            seen.append(self.m.read_marker())
            return 0
        self.m.locked_run = fake_locked_run
        self.m.resume_interrupted()
        self.assertEqual(seen[0]["attempt"], 2)

    def test_failure_before_the_body_on_resume_leaves_no_marker(self):
        # A failed backup-due.py is a failure, not a kill: no second
        # "interrupted twice" alert an hour later.
        self.write_marker(attempt=1)
        self.due_code = 3
        with self.assertRaises(self.m.Fail):
            self.m.resume_interrupted()
        self.assertIsNone(self.m.read_marker())
        self.assertEqual(self.m.resume_interrupted(), 0)

    def test_non_dict_marker_reruns(self):
        with open(self.m.RUNNING, "w") as fh:
            fh.write('[1, 2]')
        self.m.resume_interrupted()
        self.assertEqual(len(self.body_calls), 1)

    def test_bad_attempt_value_reruns(self):
        with open(self.m.RUNNING, "w") as fh:
            json.dump({"attempt": "x"}, fh)
        self.m.resume_interrupted()
        self.assertEqual(self.body_calls[0][0]["attempt"], 2)

    def test_unexpected_error_on_resume_messages_marveen(self):
        def boom():
            raise OSError("disk gone")
        self.m.run_backup = boom
        self.write_marker(attempt=1)
        sys.argv = ["heti-mentes-cmd.py", "--resume-interrupted"]
        self.assertEqual(self.m.main(), 1)
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("OSError", self.sent()[0][-1])

    def test_unexpected_error_on_regular_run_still_raises(self):
        def boom():
            raise OSError("disk gone")
        self.m.run_backup = boom
        sys.argv = ["heti-mentes-cmd.py"]
        with self.assertRaises(OSError):
            self.m.main()
        self.assertEqual(self.sent(), [])

    def test_alert_that_was_not_queued_is_logged(self):
        self.send_out = (0, "FAIL dashboard down\n")
        self.write_marker(attempt=self.m.RESUME_MAX_ATTEMPTS)
        sys.argv = ["heti-mentes-cmd.py", "--resume-interrupted"]
        self.assertEqual(self.m.main(), 1)
        self.assertIn("ALERT NOT QUEUED", self.log_text())

    def test_queued_alert_leaves_no_extra_log_line(self):
        self.write_marker(attempt=self.m.RESUME_MAX_ATTEMPTS)
        sys.argv = ["heti-mentes-cmd.py", "--resume-interrupted"]
        self.assertEqual(self.m.main(), 1)
        self.assertNotIn("ALERT NOT QUEUED", self.log_text())

    def test_unreadable_marker_reruns(self):
        with open(self.m.RUNNING, "w") as fh:
            fh.write("{broken")
        self.m.resume_interrupted()
        self.assertEqual(len(self.body_calls), 1)

    def test_resume_failure_messages_marveen(self):
        # A systemd timer tells nobody about exit 1, so the resume path reports itself.
        self.write_marker(attempt=self.m.RESUME_MAX_ATTEMPTS)
        sys.argv = ["heti-mentes-cmd.py", "--resume-interrupted"]
        self.assertEqual(self.m.main(), 1)
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("[MENTES]", self.sent()[0][-1])

    def test_regular_failure_does_not_message(self):
        # The 20:00 command task alerts through the runner; no second message.
        def boom():
            raise self.m.Fail("x")
        self.m.run_backup = boom
        sys.argv = ["heti-mentes-cmd.py"]
        self.assertEqual(self.m.main(), 1)
        self.assertEqual([c for c in self.calls if "scripts/agent-msg.sh" in c], [])


if __name__ == "__main__":
    unittest.main()
