#!/usr/bin/env python3
"""Unit tests for the failure half of scripts/hooks/tool-log-capture.py.

WHY THIS SUITE EXISTS
tool_call_log held 691 rows and not one with success=0 (measured 2026-09-06).
The cause was not a mis-named field: a Bash call that exits non-zero never
reaches PostToolUse at all, so only wins were ever recorded. A column that
cannot take the value 0 looks like a check and checks nothing.

The repair is the PostToolUseFailure hook event, which does fire for those
calls. Two things can silently undo it, and this suite guards both:
  1. the hook deciding "no is_error key -> success" on a failure payload;
  2. the event never being REGISTERED, which no amount of correct code fixes.

Privacy: synthetic payloads only, no real commands or agent data.
"""
import json
import os
import re
import unittest
import importlib.util

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HOOK_PATH = os.path.join(_ROOT, "scripts", "hooks", "tool-log-capture.py")

_spec = importlib.util.spec_from_file_location("tool_log_capture", _HOOK_PATH)
hook = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(hook)  # type: ignore[union-attr]


class TestSuccessVerdict(unittest.TestCase):
    """_success_from_payload(payload) -> bool"""

    def test_normal_post_tool_use_is_success(self):
        self.assertTrue(hook._success_from_payload({
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_response": {"stdout": "ok", "stderr": "", "interrupted": False},
        }))

    def test_post_tool_use_with_is_error_is_failure(self):
        self.assertFalse(hook._success_from_payload({
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_response": {"is_error": True},
        }))

    def test_failure_event_is_failure_even_without_tool_response(self):
        # The whole bug in one case: the failure payload carries `error`, not
        # `tool_response`, so anything that looks for is_error sees nothing.
        self.assertFalse(hook._success_from_payload({
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "exit 7"},
            "error": "Exit code 7",
        }))

    def test_failure_event_ignores_a_stray_tool_response(self):
        self.assertFalse(hook._success_from_payload({
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_response": {"stdout": "partial output"},
        }))

    def test_missing_event_name_keeps_the_old_permissive_reading(self):
        # Older payload shapes must not start reporting failures wholesale.
        self.assertTrue(hook._success_from_payload({
            "tool_name": "Bash",
            "tool_response": {"stdout": "ok"},
        }))


class TestSummary(unittest.TestCase):
    """_summary_for(payload) -> str"""

    def test_failure_summary_keeps_the_command(self):
        s = hook._summary_for({
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "ls /nincs-ilyen-utvonal"},
        })
        self.assertEqual(s, "ls /nincs-ilyen-utvonal")

    def test_interrupt_is_tagged_not_dropped(self):
        s = hook._summary_for({
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "sleep 900"},
            "is_interrupt": True,
        })
        self.assertTrue(s.startswith("[interrupt] "), s)
        self.assertIn("sleep 900", s)

    def test_success_summary_is_never_tagged(self):
        s = hook._summary_for({
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "is_interrupt": True,  # meaningless on a success payload
        })
        self.assertEqual(s, "echo hello")

    def test_secrets_are_still_redacted_on_the_failure_path(self):
        s = hook._summary_for({
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "curl -H 'Authorization: Bearer abcdef1234567890' http://x"},
        })
        self.assertNotIn("abcdef1234567890", s)
        self.assertIn("[REDACTED]", s)

    def test_summary_stays_within_the_column_budget(self):
        s = hook._summary_for({
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "x" * 5000},
            "is_interrupt": True,
        })
        self.assertLessEqual(len(s), 200)


class TestWiring(unittest.TestCase):
    """The event has to be REGISTERED, or the correct code above never runs.

    This is the half that a code-only test cannot see: on 2026-09-06 the logic
    for reading a failure was fine in principle and the table still could not
    hold a 0, because nothing was listening on the event.
    """

    def _hooks(self, path, substitutions=None):
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        for k, v in (substitutions or {}).items():
            raw = raw.replace(k, v)
        return json.loads(raw).get("hooks", {})

    def test_project_settings_register_the_failure_event(self):
        hooks = self._hooks(os.path.join(_ROOT, ".claude", "settings.json"))
        entries = hooks.get("PostToolUseFailure")
        self.assertIsNotNone(entries, "PostToolUseFailure not registered in .claude/settings.json")
        self.assertIn("tool-log-capture.py", json.dumps(entries))

    def test_agent_template_registers_the_failure_event(self):
        path = os.path.join(_ROOT, "templates", "settings.json.template")
        hooks = self._hooks(path, {"{{PROJECT_ROOT}}": "/tmp/x", "{{AGENT_ID}}": "x", "{{AGENT_NAME}}": "x"})
        entries = hooks.get("PostToolUseFailure")
        self.assertIsNotNone(entries, "PostToolUseFailure not registered in the agent template")
        # The template's failure listener is the effect-side skill-tree audit:
        # a script that writes and THEN crashes must not slip past it.
        self.assertIn("global-skill-write-audit.mjs", json.dumps(entries))

    def test_hook_names_the_event_it_relies_on(self):
        # If someone renames the event, the hook's own text should stop matching
        # too, rather than quietly reading every failure as a success.
        with open(_HOOK_PATH, encoding="utf-8") as f:
            src = f.read()
        self.assertTrue(re.search(r"PostToolUseFailure", src))


if __name__ == "__main__":
    unittest.main(verbosity=2)
