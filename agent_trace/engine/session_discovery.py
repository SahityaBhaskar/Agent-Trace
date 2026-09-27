"""
Session Discovery Engine
Auto-discovers AI coding agent sessions from known storage locations on the host system.
Supports: Claude Code, Antigravity, Bob, Cursor, GitHub Copilot, Aider, Codex, Windsurf.
No external deps — stdlib only.
"""
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import logging
import sqlite3

_log = logging.getLogger(__name__)


# ── Known agent paths ────────────────────────────────────────────────────────

def _home() -> Path:
    return Path.home()

AGENT_PROVIDERS: List[Dict[str, Any]] = [
    {
        "name": "Claude Code",
        "id": "claude-code",
        "icon": "🤖",
        "scan_dirs": [
            _home() / ".claude" / "projects",
            _home() / ".claude",
        ],
        "transcript_patterns": ["*.jsonl", "transcript*.jsonl", "session*.jsonl"],
        "format": "jsonl",
    },
    {
        "name": "Antigravity",
        "id": "antigravity",
        "icon": "🚀",
        "scan_dirs": [
            _home() / ".gemini" / "antigravity" / "brain",
        ],
        "transcript_patterns": ["transcript.jsonl", "transcript_full.jsonl"],
        "format": "jsonl",
    },
    {
        "name": "Bob",
        "id": "bob",
        "icon": "🧠",
        "scan_dirs": [
            _home() / ".bob" / "sessions",
            _home() / ".bob" / "history",
            _home() / ".bob",
        ],
        "transcript_patterns": ["*.jsonl", "*.json", "session_*.json"],
        "format": "jsonl",
    },
    {
        "name": "Cursor",
        "id": "cursor",
        "icon": "⚡",
        "scan_dirs": [
            _home() / "Library" / "Application Support" / "Cursor" / "User" / "workspaceStorage",
            _home() / ".cursor",
        ],
        "transcript_patterns": ["composer.json", "chat.json", "*.json"],
        "format": "json",
    },
    {
        "name": "GitHub Copilot",
        "id": "copilot",
        "icon": "🐙",
        "scan_dirs": [
            _home() / "Library" / "Application Support" / "Code" / "User" / "globalStorage" / "github.copilot-chat",
            _home() / ".copilot",
        ],
        "transcript_patterns": ["*.json", "conversation*.json"],
        "format": "json",
    },
    {
        "name": "Windsurf",
        "id": "windsurf",
        "icon": "🌊",
        "scan_dirs": [
            _home() / "Library" / "Application Support" / "Windsurf",
            _home() / ".windsurf",
        ],
        "transcript_patterns": ["*.jsonl", "session*.json"],
        "format": "jsonl",
    },
    {
        "name": "Codex",
        "id": "codex",
        "icon": "📦",
        "scan_dirs": [
            _home() / ".codex",
            _home() / ".openai" / "codex",
        ],
        "transcript_patterns": ["*.jsonl", "session_*.json"],
        "format": "jsonl",
    },
    {
        "name": "Aider",
        "id": "aider",
        "icon": "📝",
        # Aider stores chat history in the working repo itself
        "scan_dirs": [],
        "transcript_patterns": [".aider.chat.history.md"],
        "workspace_local": True,  # look in current repo root
        "format": "markdown",
    },
]


# ── Discovered session dataclass ─────────────────────────────────────────────

@dataclass
class DiscoveredSession:
    session_id: str
    agent_name: str
    agent_id: str
    agent_icon: str
    transcript_path: str
    user_prompt: str
    last_modified: float
    event_count: int
    is_active: bool            # modified within last 15 min
    project_path: Optional[str] = None
    git_root: Optional[str] = None
    file_size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["last_modified_ago"] = _human_ago(self.last_modified)
        return d


def _human_ago(ts: float) -> str:
    delta = time.time() - ts
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


# ── Prompt extractors per format ─────────────────────────────────────────────

def _extract_first_prompt_jsonl(path: Path) -> tuple[str, int]:
    """Returns (first_user_prompt, line_count). Reads up to 50 lines."""
    prompt = ""
    count = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= 200:
                    break
                line = line.strip()
                if not line:
                    continue
                count += 1
                try:
                    obj = json.loads(line)
                    # Claude Code / Antigravity style
                    if obj.get("type") == "USER_INPUT" and not prompt:
                        c = obj.get("content", "")
                        if isinstance(c, str) and c.strip():
                            prompt = c.strip()
                    # role/content style (Cursor, Bob)
                    if obj.get("role") == "user" and not prompt:
                        c = obj.get("content", "")
                        if isinstance(c, str) and c.strip():
                            prompt = c.strip()
                    # Anthropic messages API style
                    if isinstance(obj.get("messages"), list) and not prompt:
                        for m in obj["messages"]:
                            if m.get("role") == "user":
                                c = m.get("content", "")
                                if isinstance(c, str) and c.strip():
                                    prompt = c.strip()
                                    break
                except Exception:
                    continue
    except Exception:
        pass
    return prompt or "AI coding session", count


def _extract_first_prompt_json(path: Path) -> tuple[str, int]:
    """Single JSON object — look for conversation/messages arrays."""
    prompt = ""
    count = 0
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        obj = json.loads(raw)
        messages = (
            obj.get("messages")
            or obj.get("conversation")
            or obj.get("turns")
            or obj.get("history")
            or []
        )
        if isinstance(messages, list):
            count = len(messages)
            for m in messages:
                role = m.get("role", "") or m.get("type", "")
                if role in ("user", "human", "USER_INPUT"):
                    c = m.get("content") or m.get("text") or m.get("message", "")
                    if isinstance(c, str) and c.strip():
                        prompt = c.strip()
                        break
    except Exception:
        pass
    return prompt or "AI coding session", count


def _extract_first_prompt_markdown(path: Path) -> tuple[str, int]:
    """Aider .aider.chat.history.md — first #### human: block."""
    prompt = ""
    count = 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            if line.startswith("#### human:") or line.startswith("**human**:"):
                text = line.split(":", 1)[1].strip() if ":" in line else ""
                if text and not prompt:
                    prompt = text.strip()
                count += 1
    except Exception:
        pass
    return prompt or "Aider coding session", count


def _extract_project_path_from_jsonl(path: Path) -> Optional[str]:
    """Try to infer the repo path from transcript content."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 100:
                    break
                try:
                    obj = json.loads(line)
                    # Antigravity brain dirs are named by conversation ID, parent contains workspace path
                    # Check tool_calls for file paths
                    for tc in obj.get("tool_calls", []):
                        args = tc.get("arguments") or tc.get("input") or {}
                        for key in ("AbsolutePath", "TargetFile", "SearchDirectory", "DirectoryPath"):
                            v = args.get(key, "")
                            if v and v.startswith("/") and "/" in v:
                                p = Path(v)
                                for parent in p.parents:
                                    if (parent / ".git").exists():
                                        return str(parent)
                                break
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _infer_git_root(transcript_path: Path) -> Optional[str]:
    """Walk up from the transcript directory looking for a .git root."""
    try:
        p = transcript_path.parent
        for _ in range(8):
            if (p / ".git").exists():
                return str(p)
            p = p.parent
    except Exception:
        pass
    return None


# ── Core scanner ─────────────────────────────────────────────────────────────

class SessionDiscoveryEngine:
    """Scans all registered AI agent providers and returns discovered sessions."""

    ACTIVE_THRESHOLD_SECS = 15 * 60   # 15 minutes
    MAX_AGE_SECS          = 30 * 24 * 3600  # 30 days
    MAX_SESSIONS_PER_AGENT = 10

    def discover_all(
        self,
        repo_path: Optional[str] = None,
        max_age_days: int = 30,
    ) -> List[DiscoveredSession]:
        sessions: List[DiscoveredSession] = []
        max_age = max_age_days * 86400

        # 1. Pull all Bob sessions from host Bob SQLite database
        try:
            bob_sessions = self._scan_bob(max_age)
            sessions.extend(bob_sessions)
        except Exception as e:
            _log.debug("Bob session scan failed: %s", e)

        # 2. Pull other providers (skip 'bob' provider scan if bob_sessions were found)
        for provider in AGENT_PROVIDERS:
            if provider["id"] == "bob" and sessions:
                continue
            try:
                found = self._scan_provider(provider, max_age)
                sessions.extend(found)
            except Exception as e:
                _log.debug("Session discovery failed for %s: %s", provider["id"], e)

        # 3. Workspace-local scan (Aider .aider.chat.history.md in repo)
        if repo_path:
            try:
                sessions.extend(self._scan_workspace_local(repo_path, max_age))
            except Exception as e:
                _log.debug("Workspace local scan failed: %s", e)

        # Sort: Bob sessions at the top (active Bob first, then newest Bob),
        # followed by other agents (active first, then newest).
        sessions.sort(key=lambda s: (
            0 if s.agent_id == "bob" else 1,
            0 if s.is_active else 1,
            -s.last_modified
        ))
        return sessions

    def _scan_bob(self, max_age: float) -> List[DiscoveredSession]:
        """
        Pulls ALL Bob sessions from host storage at ~/.bob/db/bob.db (SQLite),
        syncing transcripts to ~/.bob/sessions/<task_id>.jsonl so downstream
        tools and AST causal engines can analyze them seamlessly.
        """
        sessions: List[DiscoveredSession] = []
        db_path = _home() / ".bob" / "db" / "bob.db"
        if not db_path.exists():
            return sessions

        now = time.time()
        sessions_dir = _home() / ".bob" / "sessions"
        try:
            sessions_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT t.id, t.title, t.first_message, t.status, t.project_id, t.directory,
                       t.created_at, t.updated_at, count(m.id) as msg_count
                FROM tasks t
                LEFT JOIN messages m ON t.id = m.task_id
                GROUP BY t.id
                ORDER BY t.updated_at DESC
            """)
            tasks = cur.fetchall()

            for task in tasks:
                task_id = task["id"]
                raw_title = (task["title"] or task["first_message"] or "").strip()
                msg_count = task["msg_count"]

                # Skip completely blank draft tasks with 0 messages and no title
                if not raw_title and msg_count == 0:
                    continue

                updated_ms = task["updated_at"] or task["created_at"] or (now * 1000)
                updated_ts = updated_ms / 1000.0
                age = now - updated_ts
                if age > max_age:
                    continue

                # Clean prompt: if title was wrapped with system or env metadata, extract the prompt
                prompt = raw_title
                if not prompt and msg_count > 0:
                    try:
                        mcur = conn.cursor()
                        mcur.execute(
                            "SELECT data FROM messages WHERE task_id = ? AND role = 'user' ORDER BY created_at ASC, rowid ASC LIMIT 1",
                            (task_id,)
                        )
                        urow = mcur.fetchone()
                        if urow:
                            ud = json.loads(urow["data"])
                            prompt = (ud.get("content") or "").strip()
                    except Exception:
                        pass

                if not prompt:
                    prompt = "Bob coding session"
                elif prompt.startswith("You are working on the agent-trace project"):
                    # Extract task title from task prompt description
                    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
                    for ln in lines:
                        if ln.startswith("## YOUR TASK:") or ln.startswith("YOUR TASK:"):
                            prompt = ln.split(":", 1)[1].strip()
                            break
                        elif ln.startswith("Task:"):
                            prompt = ln.split(":", 1)[1].strip()
                            break

                # Resolve project_path and git_root
                proj_id = task["project_id"] or ""
                directory = task["directory"] or ""
                if proj_id.startswith("file:"):
                    project_path = proj_id[5:]
                elif directory:
                    project_path = directory
                else:
                    project_path = None

                git_root = None
                if project_path:
                    git_root = _infer_git_root(Path(project_path) / "dummy") or project_path

                # Sync / export transcript to ~/.bob/sessions/<task_id>.jsonl
                t_path = sessions_dir / f"{task_id}.jsonl"
                try:
                    needs_export = not t_path.exists() or (t_path.stat().st_mtime < updated_ts)
                    if needs_export:
                        mcur = conn.cursor()
                        mcur.execute(
                            "SELECT role, data, created_at FROM messages WHERE task_id = ? ORDER BY created_at ASC, rowid ASC",
                            (task_id,)
                        )
                        msgs = mcur.fetchall()
                        with open(t_path, "w", encoding="utf-8") as f:
                            if msgs:
                                for m in msgs:
                                    try:
                                        d = json.loads(m["data"])
                                        d["agent"] = "bob"
                                        if "toolCalls" in d and "tool_calls" not in d:
                                            d["tool_calls"] = d["toolCalls"]
                                        if "created_at" not in d and m["created_at"]:
                                            d["created_at"] = m["created_at"]
                                        f.write(json.dumps(d) + "\n")
                                    except Exception:
                                        continue
                            else:
                                f.write(json.dumps({
                                    "agent": "bob",
                                    "role": "user",
                                    "content": prompt,
                                    "created_at": updated_ms,
                                }) + "\n")
                except Exception as ex:
                    _log.debug("Failed exporting Bob transcript for %s: %s", task_id, ex)

                file_size = t_path.stat().st_size if t_path.exists() else 0
                is_active = (task["status"] == "active") and (age < self.ACTIVE_THRESHOLD_SECS)

                sessions.append(DiscoveredSession(
                    session_id=f"bob__{task_id[:12]}",
                    agent_name="Bob",
                    agent_id="bob",
                    agent_icon="🧠",
                    transcript_path=str(t_path),
                    user_prompt=prompt,
                    last_modified=updated_ts,
                    event_count=max(msg_count, 1),
                    is_active=is_active,
                    project_path=project_path,
                    git_root=git_root,
                    file_size_bytes=file_size,
                ))

            conn.close()
        except Exception as e:
            _log.debug("Error querying Bob database at %s: %s", db_path, e)

        return sessions

    def discover_for_repo(self, repo_path: str, max_age_days: int = 30) -> List[DiscoveredSession]:
        """Return only sessions whose inferred git root matches repo_path."""
        all_sessions = self.discover_all(repo_path=repo_path, max_age_days=max_age_days)
        repo_real = str(Path(repo_path).resolve())
        matched = [s for s in all_sessions if s.git_root == repo_real or s.project_path == repo_real]
        return matched if matched else all_sessions  # fall back to global if none match

    # ── Provider scan ─────────────────────────────────────────────────────────

    def _scan_provider(self, provider: Dict[str, Any], max_age: float) -> List[DiscoveredSession]:
        sessions: List[DiscoveredSession] = []
        now = time.time()

        for scan_dir in provider.get("scan_dirs", []):
            scan_dir = Path(scan_dir)
            if not scan_dir.exists():
                continue
            try:
                transcripts = self._find_transcripts(scan_dir, provider["transcript_patterns"])
            except PermissionError:
                continue

            seen_keys = set()
            for t_path in transcripts:
                try:
                    stat = t_path.stat()
                    age = now - stat.st_mtime
                    if age > max_age:
                        continue

                    # Antigravity deduplication: prefer transcript_full.jsonl over transcript.jsonl
                    if provider.get("id") == "antigravity":
                        if t_path.name == "transcript.jsonl" and (t_path.parent / "transcript_full.jsonl").exists():
                            continue
                        conv_key = t_path.parents[2].name if len(t_path.parents) >= 3 else t_path.parent.name
                        if conv_key in seen_keys:
                            continue
                        seen_keys.add(conv_key)
                        session_id = f"antigravity__{conv_key[:12]}"
                    else:
                        if t_path.stem in seen_keys:
                            continue
                        seen_keys.add(t_path.stem)
                        # Stable session_id from path
                        session_id = f"{provider['id']}__{re.sub(r'[^a-zA-Z0-9]', '_', t_path.stem)}"

                    fmt = provider.get("format", "jsonl")
                    if fmt == "jsonl":
                        prompt, count = _extract_first_prompt_jsonl(t_path)
                    elif fmt == "json":
                        prompt, count = _extract_first_prompt_json(t_path)
                    elif fmt == "markdown":
                        prompt, count = _extract_first_prompt_markdown(t_path)
                    else:
                        prompt, count = "AI session", 0

                    project_path = _extract_project_path_from_jsonl(t_path) if fmt == "jsonl" else None
                    git_root = _infer_git_root(t_path) or project_path

                    sessions.append(DiscoveredSession(
                        session_id=session_id,
                        agent_name=provider["name"],
                        agent_id=provider["id"],
                        agent_icon=provider["icon"],
                        transcript_path=str(t_path),
                        user_prompt=prompt,
                        last_modified=stat.st_mtime,
                        event_count=count,
                        is_active=age < self.ACTIVE_THRESHOLD_SECS,
                        project_path=project_path,
                        git_root=git_root,
                        file_size_bytes=stat.st_size,
                    ))

                    if len(sessions) >= self.MAX_SESSIONS_PER_AGENT:
                        break

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    _log.debug("Failed scanning %s: %s", t_path, e)
                    continue

        return sessions

    def _scan_workspace_local(self, repo_path: str, max_age: float) -> List[DiscoveredSession]:
        """Scan workspace-local agent files like Aider's .aider.chat.history.md."""
        sessions: List[DiscoveredSession] = []
        now = time.time()
        repo_dir = Path(repo_path)
        aider_history = repo_dir / ".aider.chat.history.md"
        if aider_history.exists():
            try:
                stat = aider_history.stat()
                if now - stat.st_mtime <= max_age:
                    prompt, count = _extract_first_prompt_markdown(aider_history)
                    sessions.append(DiscoveredSession(
                        session_id=f"aider__{re.sub(r'[^a-zA-Z0-9]', '_', repo_dir.name)}",
                        agent_name="Aider",
                        agent_id="aider",
                        agent_icon="📝",
                        transcript_path=str(aider_history),
                        user_prompt=prompt,
                        last_modified=stat.st_mtime,
                        event_count=count,
                        is_active=(now - stat.st_mtime < self.ACTIVE_THRESHOLD_SECS),
                        project_path=str(repo_dir.resolve()),
                        git_root=str(repo_dir.resolve()),
                        file_size_bytes=stat.st_size,
                    ))
            except Exception:
                pass
        return sessions

    def _find_transcripts(self, scan_dir: Path, patterns: List[str]) -> List[Path]:
        """Find transcript files matching any of the given glob patterns up to 3 levels deep."""
        found: List[Path] = []
        try:
            for pattern in patterns:
                # glob depth-limited: *, */*.*, */*/*.* etc
                for depth in ("", "*/", "*/*/", "*/*/*/"):
                    for p in scan_dir.glob(depth + pattern):
                        if p.is_file() and p not in found:
                            found.append(p)
        except (PermissionError, OSError):
            pass
        # Sort by modification time, newest first
        found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return found[:self.MAX_SESSIONS_PER_AGENT * 2]

    # ── Auto-pick helper ──────────────────────────────────────────────────────

    def auto_pick(self, repo_path: str) -> Optional[DiscoveredSession]:
        """
        Select the best session to auto-analyze for the given repo:
        1. Most recent ACTIVE session whose git_root matches repo_path.
        2. Most recent ANY session matching repo_path.
        3. Most recent globally active session (any repo).
        """
        repo_real = str(Path(repo_path).resolve())
        all_sessions = self.discover_all(repo_path=repo_path)

        # Priority 1: active + matching repo
        for s in all_sessions:
            if s.is_active and (s.git_root == repo_real or s.project_path == repo_real):
                return s

        # Priority 2: any session matching repo
        for s in all_sessions:
            if s.git_root == repo_real or s.project_path == repo_real:
                return s

        # Priority 3: most recent active globally
        for s in all_sessions:
            if s.is_active:
                return s

        # Priority 4: top session (most recent Bob session or discovered session)
        if all_sessions:
            return all_sessions[0]

        return None


# Singleton
session_discovery = SessionDiscoveryEngine()
