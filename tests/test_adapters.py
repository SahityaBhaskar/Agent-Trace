"""Unit tests for agent transcript adapters."""
import unittest
from agent_trace.engine.adapters import (
    ClaudeCodeAdapter,
    CursorAdapter,
    GenericAdapter,
    detect_adapter,
)


class TestClaudeCodeAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = ClaudeCodeAdapter()

    def test_claude_code_user_input(self):
        line = {"type": "USER_INPUT", "content": "Add retry logic"}
        events = self.adapter.parse_line(line, seq=1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action_type"], "USER_REQUEST")

    def test_claude_code_read_tool(self):
        line = {
            "type": "AGENT_RESPONSE",
            "tool_calls": [{"name": "read_file", "arguments": {"path": "foo.py"}}],
        }
        events = self.adapter.parse_line(line, seq=2)
        action_types = [e["action_type"] for e in events]
        self.assertIn("FILE_READ", action_types)

    def test_claude_code_write_tool(self):
        line = {
            "type": "AGENT_RESPONSE",
            "tool_calls": [{"name": "write_file", "arguments": {"path": "bar.py"}}],
        }
        events = self.adapter.parse_line(line, seq=3)
        action_types = [e["action_type"] for e in events]
        self.assertIn("FILE_MODIFIED", action_types)

    def test_claude_code_long_prompt_and_thinking_not_truncated(self):
        long_prompt = "A" * 350
        long_thinking = "Thinking step by step about " + ("B" * 500)
        events_prompt = self.adapter.parse_line({"type": "USER_INPUT", "content": long_prompt}, seq=1)
        self.assertEqual(events_prompt[0]["payload"]["output_summary"], long_prompt)
        self.assertEqual(events_prompt[0]["target"]["query"], long_prompt)

        events_thinking = self.adapter.parse_line({
            "type": "PLANNER_RESPONSE",
            "thinking": long_thinking,
            "tool_calls": [{"name": "view_file", "arguments": {"AbsolutePath": "/very/long/path/to/some/deep/module/file.py"}}]
        }, seq=2)
        thinking_evt = [e for e in events_thinking if e["action_type"] == "AGENT_REASONING_NOTE"][0]
        self.assertEqual(thinking_evt["payload"]["output_summary"], long_thinking)
        tool_evt = [e for e in events_thinking if e["action_type"] == "FILE_READ"][0]
        self.assertIn("/very/long/path/to/some/deep/module/file.py", tool_evt["payload"]["output_summary"])


class TestCursorAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = CursorAdapter()

    def test_cursor_user_role(self):
        line = {"role": "user", "content": "Fix the bug"}
        events = self.adapter.parse_line(line, seq=1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action_type"], "USER_REQUEST")

    def test_cursor_tool_use_in_content(self):
        line = {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "read_file", "input": {"path": "x.py"}}
            ],
        }
        events = self.adapter.parse_line(line, seq=2)
        action_types = [e["action_type"] for e in events]
        self.assertIn("FILE_READ", action_types)

    def test_cursor_long_prompt_not_truncated(self):
        long_prompt = "A" * 350
        events = self.adapter.parse_line({"role": "user", "content": long_prompt}, seq=1)
        self.assertEqual(events[0]["payload"]["output_summary"], long_prompt)
        self.assertEqual(events[0]["target"]["query"], long_prompt)


class TestDetectAdapter(unittest.TestCase):
    def test_detect_adapter_claude(self):
        adapter = detect_adapter({"type": "USER_INPUT"})
        self.assertIsInstance(adapter, ClaudeCodeAdapter)

    def test_detect_adapter_cursor(self):
        adapter = detect_adapter({"role": "user", "content": "hello"})
        self.assertIsInstance(adapter, CursorAdapter)

    def test_detect_adapter_generic(self):
        adapter = detect_adapter({})
        self.assertIsInstance(adapter, GenericAdapter)


if __name__ == "__main__":
    unittest.main()
