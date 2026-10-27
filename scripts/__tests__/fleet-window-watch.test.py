#!/usr/bin/env python3
"""Hermetic tests for scripts/fleet-window-watch.py.

WHY THESE THREE CASES AND NOT MORE. The watcher is a gate-shaped thing: it
decides whether to speak. Two errors are possible and they cost different
amounts. Staying silent when an agent could work again is the failure the
watcher exists to prevent (measured: 23-28 idle hours, 2026-09-19). Speaking
when nothing is wrong is cheaper but not free -- a notice that fires on a
healthy morning is unread within a week, and then the real one is unread too.

So: one positive control (it CAN say yes -- an assertion that only ever says no
is not an assertion), and two negative controls, one per way of being wrong.

No dashboard, no network, no state file: every reader is stubbed, so the test
measures the DECISION and nothing else.
"""
import importlib.util
import json
import os
import tempfile
import time
import unittest
from datetime import datetime

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "fleet-window-watch.py")


def load():
    spec = importlib.util.spec_from_file_location("fleet_window_watch", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestWindowDay(unittest.TestCase):
    def test_before_the_anchor_still_belongs_to_yesterday(self):
        m = load()
        # 02:00 is the SAME budget window as the previous 06:00, and reading it
        # as a new day is how the gate used to re-alarm just after midnight.
        self.assertEqual(m.window_day(datetime(2026, 9, 23, 2, 0)), "2026-09-22")
        self.assertEqual(m.window_day(datetime(2026, 9, 23, 5, 59)), "2026-09-22")
        self.assertEqual(m.window_day(datetime(2026, 9, 23, 6, 0)), "2026-09-23")
        self.assertEqual(m.window_day(datetime(2026, 9, 23, 23, 59)), "2026-09-23")


class TestStoppedByAGate(unittest.TestCase):
    def _with_state(self, fleet=None, guard=None):
        m = load()
        files = {}
        if fleet is not None:
            files[m.FLEET_STATE] = fleet
        if guard is not None:
            files[m.GUARD_STATE] = guard
        m._read_json = lambda p, d: files.get(p, d)
        return m

    def test_positive_an_earlier_window_counts(self):
        m = self._with_state(fleet={"day": "2026-09-22", "stopped": ["cody"]})
        why = m.stopped_by_a_gate("2026-09-23")
        self.assertIn("cody", why)
        self.assertIn("2026-09-22", why["cody"])

    def test_negative_the_same_window_does_not(self):
        m = self._with_state(fleet={"day": "2026-09-23", "stopped": ["cody"]})
        self.assertEqual(m.stopped_by_a_gate("2026-09-23"), {})

    def test_the_per_agent_guard_is_read_too(self):
        m = load()
        old = time.mktime(datetime(2026, 9, 20, 11, 12).timetuple())
        files = {m.GUARD_STATE: {"cody": {"stopped_at": old}, "marveen": {}}}
        m._read_json = lambda p, d: files.get(p, d)
        why = m.stopped_by_a_gate("2026-09-23")
        self.assertIn("cody", why)
        # The main agent is never a subject here: it is the channel, the gate
        # never stops it, and a stray record must not make it one.
        self.assertNotIn("marveen", why)

    def test_a_record_with_no_timestamp_is_not_a_stop(self):
        m = load()
        files = {m.GUARD_STATE: {"cody": {"stopped_at": None, "tokens": 5}}}
        m._read_json = lambda p, d: files.get(p, d)
        self.assertEqual(m.stopped_by_a_gate("2026-09-23"), {})


class TestMessage(unittest.TestCase):
    def test_it_names_the_work(self):
        m = load()
        msg = m.build_message([("cody", "a sajat napi plafonja (2026-10-24)",
                                ["#12 planned", "msg 5"])], "2026-10-25")
        self.assertIn("Munkaja: #12 planned, msg 5.", msg)
        self.assertIn("INDITAS ELMARADT", msg)

    def test_it_no_longer_says_the_start_is_manual(self):
        # Owner tg 9191 (2026-10-25) replaced the manual start: a stale "Magamtol
        # nem inditom el" would now be a false statement in the alarm.
        m = load()
        msg = m.build_message([("cody", "x", ["#1 planned"])], "2026-10-25")
        self.assertNotIn("nem inditom", msg)
        self.assertNotIn("fleet-safe-start", msg)

    def test_no_double_hyphen_in_outgoing_text(self):
        # House style: no em dash and no ' -- ' stand-in in anything that can
        # reach the owner. The fallback path bypasses outgoing-copy-gate.py.
        m = load()
        msg = m.build_message([("cody", "a sajat napi plafonja (2026-09-20)", ["#1 planned"])],
                              "2026-09-23")
        self.assertNotIn(" -- ", msg)
        self.assertNotIn("\u2014", msg)


class TestWhoIsReported(unittest.TestCase):
    """main() end to end with every reader stubbed: positive control (a stopped
    agent with work IS reported) and the two silences (no work, night shift)."""

    def _run(self, work, paused=()):
        m = load()
        old = time.time() - 3 * 86400  # an earlier window-day whenever this runs
        files = {m.GUARD_STATE: {n: {"stopped_at": old} for n in ("cody", "roxy", "anorak")}}
        m._read_json = lambda p, d: files.get(p, d)
        m._token = lambda: "tok"
        m.not_running = lambda tok: {"cody", "roxy", "anorak"}
        m.has_work = lambda tok, n: work.get(n, [])
        m.day_start_paused = lambda n: n in paused
        sent = []
        m.send_to_main = lambda text: sent.append(text) or True
        m._write_state = lambda d: None
        m.main(["--notify"])
        return sent

    def test_positive_stopped_with_work_is_reported(self):
        sent = self._run({"cody": ["#7 planned"]})
        self.assertEqual(len(sent), 1)
        self.assertIn("cody", sent[0])
        self.assertNotIn("roxy", sent[0])

    def test_no_work_means_silence(self):
        self.assertEqual(self._run({}), [])

    def test_night_shift_is_never_reported(self):
        self.assertEqual(self._run({"anorak": ["#9 planned"]}), [])

    def test_a_rest_day_pause_is_not_a_missed_start(self):
        # 10-26 msg 13477: margo/roxy reported while their day-start timer was
        # stopped on purpose. Negative control is the positive test above.
        self.assertEqual(self._run({"roxy": ["#463 in_progress"]}, paused={"roxy"}), [])


class TestRunStateNotStatus(unittest.TestCase):
    def test_a_running_agent_whose_status_is_configured_is_not_down(self):
        # The trap this codifies: /api/agents returns status="configured" for a
        # perfectly healthy running agent. Reading `status` reports the whole
        # fleet as stopped. Measured on all five agents, 2026-09-23.
        m = load()
        m._get = lambda path, tok: [
            {"name": "cody", "runState": "running", "status": "configured"},
            {"name": "roxy", "runState": "stopped", "status": "configured"},
            {"name": "marveen", "runState": "stopped", "status": "configured"},
        ]
        down = m.not_running("tok")
        self.assertEqual(down, {"roxy"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
