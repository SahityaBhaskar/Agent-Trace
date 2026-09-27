"""
SerenaClient — thin stdio MCP client for Serena semantic code intelligence.

Transport: JSON-RPC 2.0 over stdio (one persistent subprocess per session).
The client is intentionally minimal: it manages the subprocess lifecycle,
sends tool-call requests, and normalises results.  All semantic logic lives
in the callers (DiffSymbolMapper / SemanticImpactGraphBuilder).

Fallback contract
-----------------
Every public method returns None (or []) when Serena is unavailable or the
query fails.  Callers MUST check for None and fall back to AST/text analysis.
Never raise from a Serena call into user-facing code.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ── Confidence constants (used by callers when building graph edges) ─────────
CONFIDENCE_SERENA_REFERENCE   = 0.98
CONFIDENCE_SERENA_DECLARATION = 0.98
CONFIDENCE_SERENA_SYMBOLS     = 0.97
CONFIDENCE_SERENA_IMPL        = 0.96
CONFIDENCE_AST                = 0.90
CONFIDENCE_TEXT_SEARCH        = 0.50
CONFIDENCE_HEURISTIC          = 0.30


class SerenaClient:
    """
    Persistent-subprocess MCP client for Serena.

    Usage
    -----
    client = SerenaClient()
    ok = client.activate_project("/path/to/repo")
    syms = client.get_symbols_overview("src/foo.py")
    refs = client.find_referencing_symbols("src/foo.py", "MyFunction")
    client.shutdown()
    """

    def __init__(
        self,
        serena_cmd: str = "serena",
        context: str = "jb-copilot-plugin",
        startup_timeout: float = 12.0,
        tool_timeout: float = 15.0,
        lsp_warmup_delay: float = 2.0,
    ):
        self._cmd = serena_cmd
        self._context = context
        self._startup_timeout = startup_timeout
        self._tool_timeout = tool_timeout
        self._lsp_warmup_delay = lsp_warmup_delay

        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._initialized = False
        self._start_failed = False
        self._active_project: Optional[str] = None
        # Track project activation time so we can wait for LSP warmup
        self._project_activated_at: float = 0.0
        # Result cache: key → raw text response. Avoids duplicate Serena calls
        # within one analysis session. Cleared when project changes.
        self._cache: Dict[str, Optional[str]] = {}

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _ensure_started(self) -> bool:
        """Start the subprocess and run MCP initialize handshake if not done yet."""
        if self._start_failed:
            return False
        if self._initialized and self._proc and self._proc.poll() is None:
            return True
        try:
            self._proc = subprocess.Popen(
                [self._cmd, "start-mcp-server", f"--context={self._context}"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # MCP initialize handshake
            resp = self._rpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "agent-trace", "version": "1.0"},
                },
                timeout=self._startup_timeout,
            )
            if resp and "result" in resp:
                self._initialized = True
                # Send initialized notification (fire-and-forget)
                self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
                log.debug("Serena MCP initialized: %s", resp["result"].get("serverInfo", {}))
                return True
        except Exception as e:
            self._start_failed = True
            log.warning("Serena startup failed: %s", e)
        return False

    def shutdown(self):
        """Terminate the Serena subprocess."""
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                self._proc.wait(timeout=3)
        except Exception:
            pass
        self._proc = None
        self._initialized = False
        self._active_project = None

    def is_available(self) -> bool:
        """Return True if Serena is reachable."""
        return self._ensure_started()

    # ── Low-level transport ──────────────────────────────────────────────────

    def _send(self, message: dict):
        """Write one JSON-RPC message to stdin (no response expected)."""
        if self._proc and self._proc.stdin:
            line = json.dumps(message) + "\n"
            self._proc.stdin.write(line.encode())
            self._proc.stdin.flush()

    def _rpc(self, method: str, params: dict, timeout: float = 30.0) -> Optional[dict]:
        """
        Send a JSON-RPC request and read the matching response.
        Returns the full response dict or None on error/timeout.
        """
        if not self._proc or self._proc.stdin is None or self._proc.stdout is None:
            return None
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            req = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n"
            try:
                self._proc.stdin.write(req.encode())
                self._proc.stdin.flush()
            except Exception as e:
                log.warning("Serena write error: %s", e)
                return None

            # Read lines until we find the response for our id
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    # Non-blocking readline via select on macOS/Linux
                    import select
                    ready, _, _ = select.select([self._proc.stdout], [], [], min(1.0, deadline - time.monotonic()))
                    if not ready:
                        continue
                    line = self._proc.stdout.readline()
                    if not line:
                        break
                    data = json.loads(line.decode("utf-8", errors="replace"))
                    if data.get("id") == req_id:
                        return data
                    # discard notifications / other messages
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    log.debug("Serena read error: %s", e)
                    break
            log.warning("Serena timeout on method=%s id=%d", method, req_id)
            return None

    def _call_tool(self, tool_name: str, arguments: dict, timeout: float = 0.0) -> Optional[Any]:
        """
        Call a Serena tool and return the parsed content, or None on failure.
        Serena returns: {"result": {"content": [{"type":"text","text":"..."}]}}
        timeout=0 means use self._tool_timeout.
        """
        if not self._ensure_started():
            return None
        effective_timeout = timeout if timeout > 0 else self._tool_timeout
        resp = self._rpc("tools/call", {"name": tool_name, "arguments": arguments}, timeout=effective_timeout)
        if resp is None:
            return None
        if "error" in resp:
            log.debug("Serena tool error %s: %s", tool_name, resp["error"])
            return None
        result = resp.get("result", {})
        content = result.get("content", [])
        if not content:
            return None
        # Concatenate all text blocks
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else None

    # ── Project management ───────────────────────────────────────────────────

    def activate_project(self, repo_path: str) -> bool:
        """
        Tell Serena which project/repo to work on.
        Must be called before any symbol queries.

        After a fresh activation, waits `lsp_warmup_delay` seconds so the
        language server has time to finish indexing before we fire tool calls.
        Returns True on success.
        """
        if self._active_project == repo_path:
            return True  # already active
        result = self._call_tool("activate_project", {"project": repo_path}, timeout=20.0)
        if result is not None:
            self._active_project = repo_path
            self._project_activated_at = time.monotonic()
            self._cache.clear()  # invalidate cache for new project
            # Give the LSP backend time to index the project before queries arrive
            if self._lsp_warmup_delay > 0:
                log.debug("Serena project activated, waiting %.1fs for LSP warmup…", self._lsp_warmup_delay)
                time.sleep(self._lsp_warmup_delay)
            log.debug("Serena activated project: %s", repo_path)
            return True
        log.warning("Serena could not activate project: %s", repo_path)
        return False

    def _cached(self, cache_key: str, tool_name: str, arguments: dict) -> Optional[str]:
        """
        Look up cache_key; if missing call the tool, store result, and return it.
        A None result (timeout/error) is also stored so we don't retry endlessly.
        """
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = self._call_tool(tool_name, arguments)
        self._cache[cache_key] = result
        return result

    # ── Semantic queries ─────────────────────────────────────────────────────

    def get_symbols_overview(self, relative_path: str) -> Optional[str]:
        """
        Return Serena's symbol overview for a file (raw text).
        Contains function/class/method declarations with line numbers.
        """
        return self._cached(
            f"syms:{relative_path}",
            "get_symbols_overview",
            {"relative_path": relative_path},
        )

    def find_symbol(self, name_path: str, relative_path: Optional[str] = None) -> Optional[str]:
        """
        Find a symbol by name path (e.g. 'MyClass/my_method').
        Returns raw Serena text output or None.
        """
        args: Dict[str, Any] = {"name_path": name_path}
        if relative_path:
            args["relative_path"] = relative_path
        return self._cached(f"sym:{relative_path}:{name_path}", "find_symbol", args)

    def find_referencing_symbols(
        self,
        name_path: str,
        relative_path: Optional[str] = None,
    ) -> Optional[str]:
        """
        Find all symbols in the project that REFERENCE the given symbol.
        Returns raw Serena text output or None.
        """
        args: Dict[str, Any] = {"name_path": name_path}
        if relative_path:
            args["relative_path"] = relative_path
        return self._cached(f"refs:{relative_path}:{name_path}", "find_referencing_symbols", args)

    def find_declaration(self, name_path: str, relative_path: Optional[str] = None) -> Optional[str]:
        """
        Find the declaration site of a symbol.
        Returns raw Serena text output or None.
        """
        args: Dict[str, Any] = {"name_path": name_path}
        if relative_path:
            args["relative_path"] = relative_path
        return self._cached(f"decl:{relative_path}:{name_path}", "find_declaration", args)

    def find_implementations(self, name_path: str, relative_path: Optional[str] = None) -> Optional[str]:
        """
        Find all implementations of an interface / abstract symbol.
        Returns raw Serena text output or None.
        """
        args: Dict[str, Any] = {"name_path": name_path}
        if relative_path:
            args["relative_path"] = relative_path
        return self._cached(f"impl:{relative_path}:{name_path}", "find_implementations", args)

    # ── Text output parsers ──────────────────────────────────────────────────

    @staticmethod
    def parse_symbols_overview(raw: str) -> List[Dict[str, Any]]:
        """
        Parse Serena's get_symbols_overview text into a list of dicts:
          {name, kind, file, start_line, end_line}

        Serena returns structured text like:
          ## file.py
          - MyClass (class) [line 10-50]
            - my_method (method) [line 15-25]

        We parse it leniently — every line with a name + line range is extracted.
        """
        import re
        symbols: List[Dict[str, Any]] = []
        if not raw:
            return symbols

        current_file = ""
        # patterns: "- symbol_name (kind) [line X-Y]" or "- symbol_name [line X]"
        sym_re = re.compile(
            r"[-•*]\s+`?([A-Za-z_][A-Za-z0-9_.]*)`?"  # name
            r"(?:\s+\(([^)]+)\))?"                      # optional (kind)
            r"(?:\s+\[(?:line\s+)?(\d+)(?:[–\-](\d+))?\])?",  # optional [line X-Y]
        )
        file_re = re.compile(r"^#{1,3}\s+(.+)")

        for line in raw.splitlines():
            fm = file_re.match(line.strip())
            if fm:
                current_file = fm.group(1).strip()
                continue
            m = sym_re.search(line)
            if m:
                name = m.group(1)
                kind = (m.group(2) or "symbol").lower()
                start = int(m.group(3)) if m.group(3) else 0
                end = int(m.group(4)) if m.group(4) else start
                symbols.append({
                    "name": name,
                    "kind": kind,
                    "file": current_file,
                    "start_line": start,
                    "end_line": end,
                })
        return symbols

    @staticmethod
    def parse_referencing_symbols(raw: str) -> List[Dict[str, Any]]:
        """
        Parse find_referencing_symbols output into:
          {name, file, line, kind, snippet}

        Serena returns something like:
          ## src/client.py
          - configureInterceptors (function) [line 42]
            ```
            configureInterceptors(axios)
            ```
        """
        import re
        refs: List[Dict[str, Any]] = []
        if not raw:
            return refs

        current_file = ""
        file_re = re.compile(r"^#{1,3}\s+(.+)")
        ref_re = re.compile(
            r"[-•*]\s+`?([A-Za-z_][A-Za-z0-9_.]*)`?"
            r"(?:\s+\(([^)]+)\))?"
            r"(?:\s+\[(?:line\s+)?(\d+)\])?",
        )
        for line in raw.splitlines():
            fm = file_re.match(line.strip())
            if fm:
                current_file = fm.group(1).strip()
                continue
            m = ref_re.search(line)
            if m:
                name = m.group(1)
                kind = (m.group(2) or "symbol").lower()
                lineno = int(m.group(3)) if m.group(3) else 0
                refs.append({
                    "name": name,
                    "kind": kind,
                    "file": current_file,
                    "line": lineno,
                })
        return refs


# ── Module-level singleton ───────────────────────────────────────────────────

_client_instance: Optional[SerenaClient] = None
_instance_lock = threading.Lock()


def get_serena_client() -> SerenaClient:
    """Return a module-level singleton SerenaClient (lazy-initialised)."""
    global _client_instance
    with _instance_lock:
        if _client_instance is None:
            _client_instance = SerenaClient(
                serena_cmd=os.environ.get("SERENA_CMD", "serena"),
                context=os.environ.get("SERENA_CONTEXT", "jb-copilot-plugin"),
            )
        return _client_instance
