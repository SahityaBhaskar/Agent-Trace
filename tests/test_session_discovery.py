"""Unit tests for the SessionDiscoveryEngine and adapters."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_trace.engine.session_discovery import (
    SessionDiscoveryEngine,
    DiscoveredSession,
    AGENT_PROVIDERS,
    _extract_first_prompt_jsonl,
    _extract_first_prompt_json,
    _extract_first_prompt_markdown,
    _human_ago,
)
from agent_trace.engine.adapters import BobAdapter, detect_adapter


class TestSessionDiscoveryExtractors(unittest.TestCase):
    def test_human_ago_seconds(self):
        import time
        now = time.time()
        self.assertEqual(_human_ago(now - 10), "10s ago")
        self.assertEqual(_human_ago(now - 120), "2m ago")
        self.assertEqual(_human_ago(now - 7200), "2h ago")
        self.assertEqual(_human_ago(now - 100000), "1d ago")

    def test_extract_first_prompt_jsonl_claude(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "USER_INPUT", "content": "Fix the retry loop in payment processor"}) + "\n")
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "Looking at payment.py"}) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            prompt, count = _extract_first_prompt_jsonl(path)
            self.assertEqual(prompt, "Fix the retry loop in payment processor")
            self.assertEqual(count, 2)
        finally:
            path.unlink(missing_ok=True)

    def test_extract_first_prompt_jsonl_cursor_role(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"role": "user", "content": "Add unit tests for auth"}) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            prompt, count = _extract_first_prompt_jsonl(path)
            self.assertEqual(prompt, "Add unit tests for auth")
            self.assertEqual(count, 1)
        finally:
            path.unlink(missing_ok=True)

    def test_extract_first_prompt_json_copilot(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            data = {
                "conversation": [
                    {"role": "user", "content": "Explain this function"}
                ]
            }
            f.write(json.dumps(data))
            f.flush()
            path = Path(f.name)

        try:
            prompt, count = _extract_first_prompt_json(path)
            self.assertEqual(prompt, "Explain this function")
            self.assertEqual(count, 1)
        finally:
            path.unlink(missing_ok=True)

    def test_extract_first_prompt_markdown_aider(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
            f.write("# Chat log\n#### human: Refactor database connection\nI want to pool connections.\n")
            f.flush()
            path = Path(f.name)

        try:
            prompt, count = _extract_first_prompt_markdown(path)
            self.assertEqual(prompt, "Refactor database connection")
            self.assertEqual(count, 1)
        finally:
            path.unlink(missing_ok=True)


class TestBobAdapter(unittest.TestCase):
    def test_bob_adapter_parses_user_line(self):
        adapter = BobAdapter()
        line = {"role": "user", "content": "Fix database timeout"}
        events = adapter.parse_line(line, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action_type"], "USER_REQUEST")
        self.assertEqual(events[0]["target"]["query"], "Fix database timeout")

    def test_bob_adapter_parses_openai_tool_calls(self):
        adapter = BobAdapter()
        line = {
            "role": "assistant",
            "tool_calls": [
                {
                    "name": "edit_file",
                    "arguments": {"TargetFile": "src/api.py"}
                }
            ]
        }
        events = adapter.parse_line(line, 2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action_type"], "FILE_MODIFIED")
        self.assertEqual(events[0]["target"]["file_path"], "src/api.py")

    def test_detect_adapter_identifies_bob(self):
        adapter = detect_adapter({"agent": "bob", "step": 1})
        self.assertIsInstance(adapter, BobAdapter)

        adapter2 = detect_adapter({"function_call": {"name": "run"}})
        self.assertIsInstance(adapter2, BobAdapter)

    def test_bob_adapter_parses_camelcase_tool_calls(self):
        adapter = BobAdapter()
        line = {
            "role": "assistant",
            "toolCalls": [
                {
                    "name": "apply_diff",
                    "arguments": {"path": "agent_trace/web/static/index.html", "diff": "..."}
                }
            ]
        }
        events = adapter.parse_line(line, 3)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action_type"], "FILE_MODIFIED")
        self.assertEqual(events[0]["target"]["file_path"], "agent_trace/web/static/index.html")


class TestSessionDiscoveryEngine(unittest.TestCase):
    def test_discover_all_returns_list(self):
        engine = SessionDiscoveryEngine()
        sessions = engine.discover_all()
        self.assertIsInstance(sessions, list)
        for s in sessions:
            self.assertIsInstance(s, DiscoveredSession)
            self.assertTrue(hasattr(s, "session_id"))
            self.assertTrue(hasattr(s, "agent_name"))
            self.assertTrue(hasattr(s, "transcript_path"))

    def test_bob_sessions_prioritized_at_top(self):
        engine = SessionDiscoveryEngine()
        sessions = engine.discover_all()
        bob_sessions = [s for s in sessions if s.agent_id == "bob"]
        if bob_sessions:
            # If Bob sessions exist on host system, first session must be Bob
            self.assertEqual(sessions[0].agent_id, "bob")
            # All Bob sessions should appear before any non-Bob sessions
            first_non_bob_idx = None
            for idx, s in enumerate(sessions):
                if s.agent_id != "bob":
                    first_non_bob_idx = idx
                    break
            if first_non_bob_idx is not None:
                for idx in range(first_non_bob_idx, len(sessions)):
                    self.assertNotEqual(sessions[idx].agent_id, "bob", "Bob session found after non-Bob session")

    def test_auto_pick_fallback(self):
        engine = SessionDiscoveryEngine()
        # Should not crash on any path
        picked = engine.auto_pick("/nonexistent/repo")
        self.assertTrue(picked is None or isinstance(picked, DiscoveredSession))


if __name__ == "__main__":
    unittest.main()
