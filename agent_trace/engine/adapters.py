"""
Agent-agnostic transcript adapters.
Each adapter normalises a raw JSONL line (already parsed as a dict) from a specific
coding agent into a list of SessionEvent dicts ready for SessionEvent(**d) construction.
"""
import re
from typing import List, Dict, Any, Optional

# ── Shared helpers ────────────────────────────────────────────────────────────

def _tool_to_action(tool_name: str, args: dict) -> tuple[str, dict]:
    """Map a tool call to (action_type, target_kwargs).
    Returns a generic target dict with optional keys: file_path, query, command.
    """
    n = tool_name.lower()
    if any(k in n for k in ("read_file", "view", "read", "cat", "open")):
        file_path = (
            args.get("AbsolutePath") or args.get("path") or args.get("file_path") or
            args.get("relative_path") or args.get("target_file") or ""
        )
        return "FILE_READ", {"file_path": file_path}
    if any(k in n for k in ("write", "edit", "replace", "create_file", "apply_patch", "patch", "diff")):
        file_path = (
            args.get("TargetFile") or args.get("path") or args.get("file_path") or
            args.get("relative_path") or args.get("target_file") or ""
        )
        return "FILE_MODIFIED", {"file_path": file_path}
    if any(k in n for k in ("grep", "search", "find", "ripgrep", "symbol_search", "codebase_search")):
        query = (
            args.get("Query") or args.get("query") or
            args.get("pattern") or args.get("name_path_pattern") or
            args.get("filter_path") or args.get("relative_path") or
            args.get("path") or args.get("name") or ""
        )
        return "REPOSITORY_SEARCH", {"query": query}
    if any(k in n for k in ("bash", "shell", "run_command", "execute", "terminal", "command")):
        return "COMMAND_EXECUTED", {"command": args.get("CommandLine") or args.get("command") or args.get("cmd", "")}
    if any(k in n for k in ("test", "pytest", "jest", "mocha")):
        return "TEST_EXECUTED", {"command": args.get("command", tool_name)}
    return "COMMAND_EXECUTED", {"command": tool_name}


class ClaudeCodeAdapter:
    """Parses Claude Code / Antigravity JSONL transcript lines."""
    agent_id = "claude_code"

    def parse_line(self, data: dict, seq: int) -> List[Dict[str, Any]]:
        events = []
        step_type = data.get("type", "")
        content = str(data.get("content", ""))

        if step_type == "USER_INPUT":
            events.append({
                "event_id": f"evt_{self.agent_id}_{seq}_user",
                "session_id": "live_session",
                "sequence_number": seq,
                "timestamp": data.get("timestamp", "00:00:00"),
                "action_type": "USER_REQUEST",
                "epistemic_status": "DECLARED",
                "target": {"query": content.strip()},
                "payload": {"output_summary": content.strip()},
            })

        # ── Capture agent's verbatim chain-of-thought reasoning ───────────
        # The Antigravity/Claude Code transcript stores the model's internal
        # reasoning in a top-level `thinking` field per step. This is ground
        # truth (DECLARED) — not AI-reconstructed — so we preserve it as an
        # AGENT_REASONING_NOTE event paired with the tool calls that follow.
        thinking = data.get("thinking", "")
        if thinking and str(thinking).strip():
            events.append({
                "event_id": f"evt_{self.agent_id}_{seq}_thinking",
                "session_id": "live_session",
                "sequence_number": seq,
                "timestamp": data.get("timestamp", "00:00:00"),
                "action_type": "AGENT_REASONING_NOTE",
                "epistemic_status": "DECLARED",
                "target": {"query": str(thinking)},
                "payload": {"output_summary": str(thinking)},
            })

        for tc in data.get("tool_calls", []) or data.get("tool_use", []):
            tool_name = tc.get("name", "") or tc.get("tool", "")
            args = tc.get("arguments", tc.get("input", {})) or {}
            action, target = _tool_to_action(tool_name, args)
            events.append({
                "event_id": f"evt_{self.agent_id}_{seq}_{tool_name}",
                "session_id": "live_session",
                "sequence_number": seq,
                "timestamp": data.get("timestamp", "00:00:01"),
                "action_type": action,
                "epistemic_status": "OBSERVED",
                "target": target,
                "payload": {"output_summary": f"{tool_name}: {str(args)}"},
            })
        return events


class CursorAdapter:
    """Parses Cursor agent JSONL transcript lines (role/content format)."""
    agent_id = "cursor"

    def parse_line(self, data: dict, seq: int) -> List[Dict[str, Any]]:
        events = []
        role = data.get("role", "")
        content = data.get("content", "")

        if role == "user" and isinstance(content, str) and content.strip():
            events.append({
                "event_id": f"evt_{self.agent_id}_{seq}_user",
                "session_id": "live_session",
                "sequence_number": seq,
                "timestamp": data.get("timestamp", "00:00:00"),
                "action_type": "USER_REQUEST",
                "epistemic_status": "DECLARED",
                "target": {"query": content.strip()},
                "payload": {"output_summary": content.strip()},
            })

        # ── Capture verbatim reasoning ────────────────────────────────────
        # Cursor may carry thinking as a top-level field or as a content
        # list item with type "thinking".
        thinking = data.get("thinking", "")
        if not thinking and isinstance(content, list):
            thinking_items = [i.get("thinking", "") for i in content if isinstance(i, dict) and i.get("type") == "thinking"]
            thinking = " ".join(t for t in thinking_items if t)
        if thinking and str(thinking).strip():
            events.append({
                "event_id": f"evt_{self.agent_id}_{seq}_thinking",
                "session_id": "live_session",
                "sequence_number": seq,
                "timestamp": data.get("timestamp", "00:00:00"),
                "action_type": "AGENT_REASONING_NOTE",
                "epistemic_status": "DECLARED",
                "target": {"query": str(thinking)},
                "payload": {"output_summary": str(thinking)},
            })

        # Cursor embeds tool calls in content list items
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    tool_name = item.get("name", "")
                    args = item.get("input", {}) or {}
                    action, target = _tool_to_action(tool_name, args)
                    events.append({
                        "event_id": f"evt_{self.agent_id}_{seq}_{tool_name}",
                        "session_id": "live_session",
                        "sequence_number": seq,
                        "timestamp": data.get("timestamp", "00:00:01"),
                        "action_type": action,
                        "epistemic_status": "OBSERVED",
                        "target": target,
                        "payload": {"output_summary": f"{tool_name}: {str(args)}"},
                    })
        return events


class BobAdapter:
    """Parses Bob session events (supports both OpenAI-style tool calls and step events)."""
    agent_id = "bob"

    def parse_line(self, data: dict, seq: int) -> List[Dict[str, Any]]:
        events = []
        role = data.get("role", "")
        content = data.get("content", "")

        if role == "user" and isinstance(content, str) and content.strip():
            events.append({
                "event_id": f"evt_bob_{seq}_user",
                "session_id": "live_session",
                "sequence_number": seq,
                "timestamp": data.get("timestamp", data.get("created_at", "00:00:00")),
                "action_type": "USER_REQUEST",
                "epistemic_status": "DECLARED",
                "target": {"query": content.strip()},
                "payload": {"output_summary": content.strip()},
            })

        # OpenAI function/tool calls format or Bob toolCalls format
        tool_calls = data.get("tool_calls") or data.get("toolCalls") or []
        if not tool_calls and "function_call" in data:
            tool_calls = [{"name": data["function_call"].get("name"), "arguments": data["function_call"].get("arguments", {})}]

        for tc in tool_calls:
            tool_name = tc.get("function", {}).get("name") or tc.get("name", "")
            raw_args = tc.get("function", {}).get("arguments") or tc.get("arguments", {})
            if isinstance(raw_args, str):
                import json as _j
                try:
                    args = _j.loads(raw_args)
                except Exception:
                    args = {"raw": raw_args}
            else:
                args = raw_args or {}
            action, target = _tool_to_action(tool_name, args)
            events.append({
                "event_id": f"evt_bob_{seq}_{tool_name}",
                "session_id": "live_session",
                "sequence_number": seq,
                "timestamp": data.get("timestamp", "00:00:01"),
                "action_type": action,
                "epistemic_status": "OBSERVED",
                "target": target,
                "payload": {"output_summary": f"{tool_name}: {str(args)}"},
            })

        # Step-based format: { "action": "edit_file", "path": "...", ... }
        if "action" in data and not tool_calls:
            act = data.get("action", "")
            action, target = _tool_to_action(act, data)
            events.append({
                "event_id": f"evt_bob_{seq}_{act}",
                "session_id": "live_session",
                "sequence_number": seq,
                "timestamp": data.get("timestamp", "00:00:01"),
                "action_type": action,
                "epistemic_status": "OBSERVED",
                "target": target,
                "payload": {"output_summary": f"{act}: {str(data)}"},
            })

        return events


class GenericAdapter:
    """Fallback adapter for unknown agent formats — best-effort parsing."""
    agent_id = "generic"

    def parse_line(self, data: dict, seq: int) -> List[Dict[str, Any]]:
        events = []
        for key in ("tool_calls", "tool_use", "actions", "steps"):
            items = data.get(key, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                tool_name = item.get("name") or item.get("tool") or item.get("action", "unknown")
                args = item.get("arguments") or item.get("input") or item.get("params") or {}
                action, target = _tool_to_action(tool_name, args)
                events.append({
                    "event_id": f"evt_generic_{seq}_{tool_name}",
                    "session_id": "live_session",
                    "sequence_number": seq,
                    "timestamp": data.get("timestamp", "00:00:00"),
                    "action_type": action,
                    "epistemic_status": "OBSERVED",
                    "target": target,
                    "payload": {"output_summary": f"{tool_name}: {str(args)}"},
                })
        return events


def detect_adapter(first_line: dict):
    """Heuristically detect which agent produced this transcript."""
    if "agent" in first_line and first_line.get("agent") == "bob":
        return BobAdapter()
    if "function_call" in first_line:
        return BobAdapter()
    if "tool_calls" in first_line or first_line.get("type") in ("USER_INPUT", "AGENT_RESPONSE", "TOOL_RESULT"):
        return ClaudeCodeAdapter()
    if "role" in first_line and "content" in first_line:
        return CursorAdapter()
    return GenericAdapter()

