#!/usr/bin/env python3
"""Hermetic tests for scripts/kanban-heti-lista.py (#433, owner msg 5964).

The script is a gate that decides whether to speak once a week. Zero stale
cards must stay silent, N cards must produce exactly ONE message, and the two
failure exits (dashboard unreadable = 1, Telegram not ok = 2) must stay apart,
because the timer log is the only place anyone will see them.

No dashboard, no network: fetch_stale and send are stubbed, and REPO points at
a temp dir so the URL lookup reads files we wrote.
"""
import importlib.util
import json
import os
import tempfile
import unittest
import urllib.error

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "kanban-heti-lista.py")


def load(stale=None, fetch_error=None, send_ok=True, repo=None):
    spec = importlib.util.spec_from_file_location("kanban_heti_lista", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.sent = []

    def fake_fetch(days):
        if fetch_error:
            raise fetch_error
        return stale

    def fake_send(text):
        m.sent.append(text)
        return send_ok

    m.fetch_stale = fake_fetch
    m.send = fake_send
    m.REPO = repo or tempfile.mkdtemp()
    return m


class TestWeeklyList(unittest.TestCase):
    def test_zero_cards_sends_nothing(self):
        m = load(stale={"count": 0, "cards": []})
        self.assertEqual(m.main([]), 0)
        self.assertEqual(m.sent, [])

    def test_n_cards_send_exactly_one_line_with_the_link(self):
        m = load(stale={"count": 7, "cards": []})
        self.assertEqual(m.main([]), 0)
        self.assertEqual(len(m.sent), 1)
        self.assertIn("7", m.sent[0])
        self.assertIn("?review=stale#kanban", m.sent[0])
        self.assertNotIn("\n", m.sent[0])
        # The outgoing copy gate refuses " -- " as a dash substitute.
        self.assertNotIn(" -- ", m.sent[0])

    def test_dry_run_prints_and_does_not_send(self):
        m = load(stale={"count": 2, "cards": []})
        self.assertEqual(m.main(["--dry-run"]), 0)
        self.assertEqual(m.sent, [])

    def test_fetch_error_exits_1_without_sending(self):
        m = load(fetch_error=urllib.error.URLError("refused"))
        self.assertEqual(m.main([]), 1)
        self.assertEqual(m.sent, [])

    def test_telegram_failure_exits_2(self):
        m = load(stale={"count": 3, "cards": []}, send_ok=False)
        self.assertEqual(m.main([]), 2)


class TestDashboardUrl(unittest.TestCase):
    def _repo(self, env=None, override=None):
        repo = tempfile.mkdtemp()
        os.makedirs(os.path.join(repo, "store"))
        if env is not None:
            with open(os.path.join(repo, ".env"), "w") as f:
                f.write(env)
        if override is not None:
            with open(os.path.join(repo, "store", "config-overrides.json"), "w") as f:
                json.dump(override, f)
        return repo

    def test_falls_back_to_localhost(self):
        m = load(repo=self._repo())
        self.assertEqual(m.dashboard_url(), "http://localhost:3420")

    def test_env_value_is_used(self):
        m = load(repo=self._repo(env="DASHBOARD_PUBLIC_URL=https://dash.example/\n"))
        self.assertEqual(m.dashboard_url(), "https://dash.example")

    def test_settings_override_wins_over_env(self):
        # Same precedence as cfg() in src/config.ts.
        m = load(repo=self._repo(env="DASHBOARD_PUBLIC_URL=https://env.example\n",
                                 override={"DASHBOARD_PUBLIC_URL": "https://saved.example/"}))
        self.assertEqual(m.dashboard_url(), "https://saved.example")

    def test_link_uses_the_resolved_url(self):
        m = load(stale={"count": 1, "cards": []},
                 repo=self._repo(env="DASHBOARD_PUBLIC_URL=https://dash.example\n"))
        m.main([])
        self.assertIn("https://dash.example/?review=stale#kanban", m.sent[0])


if __name__ == "__main__":
    unittest.main()
