#!/usr/bin/env python3
"""stalled-agent-guard: can the meter say BOTH yes and no, on every branch."""
import importlib.util
import os
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "stalled-agent-guard.py")


def load():
    spec = importlib.util.spec_from_file_location("stalled_agent_guard", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


G = load()
T0 = 1_000_000.0
IDLE = "some output\n❯ \n  bypass permissions on"
ERR = "safeguards stopped the response\n  ⎿  Interrupted\n❯ "
BUSY = "· Frosting… (4s)\n  bypass permissions on · esc to interrupt"


def after(text, minutes, cards=(), pending=()):
    st, _, _ = G.assess(None, text, T0, list(cards), list(pending))
    return G.assess(st, text, T0 + minutes * 60, list(cards), list(pending))


class TestAssess(unittest.TestCase):
    def test_error_pane_with_card_alerts_owner_worthy(self):
        # 2026-11-03: cody, #777, safeguards stop at 09:04, idle until ~12:40.
        st, stalled, reason = after(ERR, 11, cards=["#777 x"])
        self.assertTrue(stalled)
        self.assertTrue(st["owner_worthy"])
        self.assertIn("safeguards stopped", reason)

    def test_error_pane_too_fresh_is_quiet(self):
        self.assertFalse(after(ERR, 5, cards=["#777 x"])[1])

    def test_unread_message_alerts_owner_worthy(self):
        st, stalled, _ = after(IDLE, 31, pending=["msg-17032"])
        self.assertTrue(stalled)
        self.assertTrue(st["owner_worthy"])

    def test_idle_with_card_only_waits_an_hour_and_stays_main_only(self):
        self.assertFalse(after(IDLE, 31, cards=["#209 ALLO"])[1])
        st, stalled, _ = after(IDLE, 61, cards=["#209 ALLO"])
        self.assertTrue(stalled)
        self.assertFalse(st["owner_worthy"])

    def test_no_work_never_alerts(self):
        self.assertFalse(after(ERR, 120)[1])
        self.assertFalse(after(IDLE, 600)[1])

    def test_busy_never_alerts(self):
        self.assertFalse(after(BUSY, 120, cards=["#1"], pending=["msg-1"])[1])

    def test_pane_change_starts_a_new_episode(self):
        st, _, _ = after(IDLE, 61, cards=["#1"])
        st["main_at"] = T0
        st2, stalled, _ = G.assess(st, IDLE + "\nnew line", T0 + 62 * 60, ["#1"], [])
        self.assertFalse(stalled)
        self.assertNotIn("main_at", st2)

    def test_empty_pane_is_quiet(self):
        self.assertFalse(after("", 600, cards=["#1"])[1])


class TestOldPending(unittest.TestCase):
    """Worksource agents: the DB row is `delivered` at enqueue, so the queue
    directory is the only place an unacknowledged message shows (#784, 3724)."""

    def test_reads_worksource_queue_by_age(self):
        with tempfile.TemporaryDirectory() as home:
            act = os.path.join(home, ".claude", "channels", "worksource", "x", "active")
            os.makedirs(act)
            old, new = os.path.join(act, "msg-1.json"), os.path.join(act, "msg-2.json")
            for p in (old, new):
                open(p, "w").close()
            now = time.time()
            os.utime(old, (now - 1800, now - 1800))
            with mock.patch.dict(os.environ, {"HOME": home}), \
                    mock.patch.object(G, "api_get", return_value=[]):
                self.assertEqual(G.old_pending("x", now), ["msg-1"])
                self.assertEqual(G.old_pending("nobody", now), [])

    def test_db_pending_still_counts(self):
        rows = [{"id": 5, "to_agent": "x", "status": "pending", "created_at": T0 - 3600},
                {"id": 6, "to_agent": "x", "status": "pending", "created_at": T0 - 60},
                {"id": 7, "to_agent": "x", "status": "delivered", "created_at": T0 - 3600}]
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(G, "api_get", return_value=rows):
            self.assertEqual(G.old_pending("x", T0), [5])


if __name__ == "__main__":
    unittest.main()
