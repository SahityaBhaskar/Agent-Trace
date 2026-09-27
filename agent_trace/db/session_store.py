"""
Session store — CRUD helpers for AgentTrace Supabase tables.

All methods are safe to call even when Supabase is not configured:
they return early with a no-op / empty result.

Tables
------
  agent_sessions          — one row per analyzed scenario
  session_events          — individual SessionEvent rows
  discovered_sessions_cache — cache of auto-discovered AI agent sessions
"""
from __future__ import annotations

import json
import logging
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .client import get_supabase_client

_log = logging.getLogger(__name__)

# ── table names ────────────────────────────────────────────────────────────────
_T_SESSIONS   = "agent_sessions"
_T_EVENTS     = "session_events"
_T_DISCOVERED = "discovered_sessions_cache"

# ── in-process arch graph LRU cache (L1) ──────────────────────────────────────
# Keyed by session_id. Survives for the server process lifetime.
# Holds at most 64 entries to avoid unbounded memory growth.
_ARCH_CACHE_MAX = 64
_arch_lru: "Dict[str, Any]" = {}
_arch_lru_order: "List[str]" = []


def _arch_lru_put(session_id: str, graph_dict: "Dict[str, Any]") -> None:
    """Insert / update an arch graph in the in-process LRU cache."""
    if session_id in _arch_lru:
        _arch_lru_order.remove(session_id)
    _arch_lru[session_id] = graph_dict
    _arch_lru_order.append(session_id)
    while len(_arch_lru_order) > _ARCH_CACHE_MAX:
        evict = _arch_lru_order.pop(0)
        _arch_lru.pop(evict, None)


def _arch_lru_get(session_id: str) -> "Optional[Dict[str, Any]]":
    """Return cached arch graph dict or None."""
    if session_id not in _arch_lru:
        return None
    # Promote to most-recently-used
    _arch_lru_order.remove(session_id)
    _arch_lru_order.append(session_id)
    return _arch_lru[session_id]


class SessionStore:
    """Thin wrapper around the Supabase client that adds error resilience."""

    # ── internal helper ───────────────────────────────────────────────────────

    def _client(self):
        return get_supabase_client()

    def _safe(self, fn, label: str):
        """Execute *fn()* and swallow all exceptions (log them at WARNING level)."""
        try:
            return fn()
        except Exception as exc:
            _log.warning("Supabase %s failed: %s", label, exc)
            return None

    # ── agent_sessions ────────────────────────────────────────────────────────

    def save_scenario(
        self,
        scenario,               # ScenarioData (Pydantic model)
        repo_path: str,
        diff_target: str = "auto",
        transcript_path: Optional[str] = None,
        agent_id: str = "unknown",
    ) -> Optional[str]:
        """
        Upsert a ScenarioData into agent_sessions.
        Returns the scenario id on success, None on failure / not configured.
        """
        client = self._client()
        if client is None:
            return None

        # Ensure unique id for transcript-based sessions so multiple sessions do not overwrite 'live-repository'
        scenario_id = scenario.id
        if transcript_path and (not scenario_id or scenario_id == "live-repository"):
            import hashlib
            scenario_id = f"session_{hashlib.sha256(transcript_path.encode()).hexdigest()[:12]}"

        # Derive repo_name from path
        import pathlib
        repo_name = pathlib.Path(repo_path).name if repo_path else "unknown"

        payload_dict = json.loads(scenario.model_dump_json())
        payload_dict["id"] = scenario_id
        stats_dict   = payload_dict.get("stats", {})

        row = {
            "id":              scenario_id,
            "repo_path":       repo_path,
            "repo_name":       repo_name,
            "title":           scenario.title,
            "description":     scenario.description,
            "user_prompt":     scenario.user_prompt,
            "diff_target":     diff_target,
            "agent_id":        agent_id,
            "transcript_path": transcript_path,
            "payload":         payload_dict,
            "stats":           stats_dict,
            "created_at":      datetime.now(timezone.utc).isoformat(),
        }

        def _do():
            return (
                client.table(_T_SESSIONS)
                .upsert(row, on_conflict="id")
                .execute()
            )

        result = self._safe(_do, f"save_scenario({scenario_id})")
        if result:
            _log.debug("Saved scenario %s to Supabase", scenario_id)
            return scenario_id
        return None

    def save_arch_graph(self, session_id: str, graph_dict: Dict[str, Any]) -> None:
        """
        Persist an architectural graph dict for a session.

        Always writes to the in-process LRU (L1).
        If Supabase is configured, also upserts into the arch_graph JSONB column
        on the agent_sessions table (L2, durable across restarts).
        """
        # L1 — always
        _arch_lru_put(session_id, graph_dict)

        # L2 — if Supabase is available
        client = self._client()
        if client is None:
            return

        def _do():
            res = (
                client.table(_T_SESSIONS)
                .update({"arch_graph": graph_dict})
                .eq("id", session_id)
                .execute()
            )
            # If the session row doesn't exist yet, upsert a placeholder row so arch_graph is persisted
            if res and not getattr(res, "data", None):
                res = (
                    client.table(_T_SESSIONS)
                    .upsert({
                        "id": session_id,
                        "repo_path": ".",
                        "repo_name": session_id,
                        "title": session_id,
                        "arch_graph": graph_dict,
                    }, on_conflict="id")
                    .execute()
                )
            return res

        self._safe(_do, f"save_arch_graph({session_id})")
        _log.debug("Saved arch_graph for session %s to Supabase", session_id)

    def get_arch_graph(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve the pre-built architectural graph for a session.

        Checks L1 in-process LRU first (instant).
        Falls back to Supabase L2 if not in LRU.
        Returns None on cache miss (caller should build fresh).
        """
        # L1 — in-process LRU
        cached = _arch_lru_get(session_id)
        if cached is not None:
            return cached

        # L2 — Supabase
        client = self._client()
        if client is None:
            return None

        def _do():
            builder = (
                client.table(_T_SESSIONS)
                .select("arch_graph")
                .eq("id", session_id)
            )
            fn = getattr(builder, "maybe_single", None) or getattr(builder, "single")
            return fn().execute()

        result = self._safe(_do, f"get_arch_graph({session_id})")
        if result and hasattr(result, "data") and result.data:
            graph = result.data.get("arch_graph")
            if graph:
                # Warm L1 so next hit is instant
                _arch_lru_put(session_id, graph)
                return graph
        return None

    def list_sessions(
        self,
        repo_path: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        List stored sessions, optionally filtered by repo_path.
        Returns lightweight rows (no full payload).
        """
        client = self._client()
        if client is None:
            return []

        def _do():
            q = (
                client.table(_T_SESSIONS)
                .select("id, repo_path, repo_name, title, description, user_prompt, diff_target, agent_id, transcript_path, stats, created_at")
                .order("created_at", desc=True)
                .limit(limit)
            )
            if repo_path:
                q = q.eq("repo_path", repo_path)
            return q.execute()

        result = self._safe(_do, "list_sessions")
        if result and hasattr(result, "data"):
            return result.data or []
        return []

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a full session (including payload) by id."""
        client = self._client()
        if client is None:
            return None

        def _do():
            return (
                client.table(_T_SESSIONS)
                .select("*")
                .eq("id", session_id)
                .single()
                .execute()
            )

        result = self._safe(_do, f"get_session({session_id})")
        if result and hasattr(result, "data"):
            return result.data
        return None

    def get_session_by_transcript(self, transcript_path: str) -> Optional[Dict[str, Any]]:
        """
        Look up a stored analysis by the source transcript_path.
        Returns the most recent matching row (lightweight — no full payload)
        so callers can decide whether a fresh analysis is needed.
        Supports matching both transcript.jsonl and transcript_full.jsonl variants.
        Returns None when Supabase is not configured or no match exists.
        """
        client = self._client()
        if client is None:
            return None

        candidates = [transcript_path]
        if "transcript.jsonl" in transcript_path:
            candidates.append(transcript_path.replace("transcript.jsonl", "transcript_full.jsonl"))
        elif "transcript_full.jsonl" in transcript_path:
            candidates.append(transcript_path.replace("transcript_full.jsonl", "transcript.jsonl"))

        def _do():
            return (
                client.table(_T_SESSIONS)
                .select("id, repo_path, title, user_prompt, stats, created_at, transcript_path")
                .in_("transcript_path", candidates)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

        result = self._safe(_do, f"get_session_by_transcript({transcript_path})")
        if result and hasattr(result, "data") and result.data:
            return result.data[0]
        return None

    def get_all_analyzed_transcripts_map(self) -> Dict[str, Dict[str, Any]]:
        """
        Return a map of {transcript_path: {id, title, user_prompt, stats, created_at}}
        for all analyzed sessions in Supabase.
        """
        client = self._client()
        if client is None:
            return {}

        def _do():
            return (
                client.table(_T_SESSIONS)
                .select("id, repo_path, title, user_prompt, stats, created_at, transcript_path")
                .not_.is_("transcript_path", "null")
                .order("created_at", desc=True)
                .limit(500)
                .execute()
            )

        result = self._safe(_do, "get_all_analyzed_transcripts_map")
        if not result or not hasattr(result, "data") or not result.data:
            return {}

        mapping: Dict[str, Dict[str, Any]] = {}
        for row in result.data:
            tp = row.get("transcript_path")
            if not tp:
                continue
            mapping[tp] = row
            # Also map alternative transcript variant for antigravity / jsonl agents
            if "transcript.jsonl" in tp:
                alt = tp.replace("transcript.jsonl", "transcript_full.jsonl")
                mapping.setdefault(alt, row)
            elif "transcript_full.jsonl" in tp:
                alt = tp.replace("transcript_full.jsonl", "transcript.jsonl")
                mapping.setdefault(alt, row)

        return mapping

    # ── session_events ────────────────────────────────────────────────────────

    def save_events(
        self,
        session_id: str,
        events: List[Any],  # List[SessionEvent]
    ) -> None:
        """Bulk-insert SessionEvent rows for a session."""
        client = self._client()
        if client is None or not events:
            return

        rows = []
        for idx, ev in enumerate(events, 1):
            ev_dict = ev.model_dump() if hasattr(ev, "model_dump") else dict(ev)
            raw_id = ev_dict.get("event_id") or str(idx)
            event_pk = f"{session_id}:{raw_id}"
            parent_id = ev_dict.get("parent_event_id")
            parent_pk = f"{session_id}:{parent_id}" if parent_id else None
            rows.append({
                "id":              event_pk,
                "session_id":      session_id,
                "sequence_number": ev_dict.get("sequence_number", idx),
                "timestamp":       ev_dict.get("timestamp"),
                "agent_id":        ev_dict.get("agent_id", "unknown"),
                "action_type":     ev_dict.get("action_type"),
                "epistemic_status": ev_dict.get("epistemic_status"),
                "target":          ev_dict.get("target"),
                "payload":         ev_dict.get("payload"),
                "parent_event_id": parent_pk,
            })

        def _do():
            return (
                client.table(_T_EVENTS)
                .upsert(rows, on_conflict="id")
                .execute()
            )

        self._safe(_do, f"save_events(session={session_id}, count={len(rows)})")
        _log.debug("Saved %d events for session %s", len(rows), session_id)

    def list_events(
        self,
        session_id: str,
    ) -> List[Dict[str, Any]]:
        """Return all events for a session ordered by sequence_number."""
        client = self._client()
        if client is None:
            return []

        def _do():
            return (
                client.table(_T_EVENTS)
                .select("*")
                .eq("session_id", session_id)
                .order("sequence_number")
                .execute()
            )

        result = self._safe(_do, f"list_events({session_id})")
        if result and hasattr(result, "data"):
            return result.data or []
        return []

    # ── discovered_sessions_cache ─────────────────────────────────────────────

    def upsert_discovered_sessions(self, sessions: List[Any]) -> None:
        """
        Cache discovered sessions.  *sessions* is a list of objects
        returned by session_discovery.discover_all() (DiscoveredSession dataclasses).
        """
        client = self._client()
        if client is None or not sessions:
            return

        host = socket.gethostname()
        now  = datetime.now(timezone.utc).isoformat()

        rows = []
        for s in sessions:
            d = s.to_dict() if hasattr(s, "to_dict") else (s if isinstance(s, dict) else {})
            rows.append({
                "host_machine":    host,
                "repo_path":       d.get("repo_path", ""),
                "provider_id":     d.get("provider_id", ""),
                "transcript_path": d.get("transcript_path", ""),
                "agent_name":      d.get("agent_name", ""),
                "last_modified":   d.get("last_modified"),
                "session_data":    d,
                "scanned_at":      now,
            })

        def _do():
            return (
                client.table(_T_DISCOVERED)
                .upsert(
                    rows,
                    on_conflict="host_machine,transcript_path",
                )
                .execute()
            )

        self._safe(_do, f"upsert_discovered_sessions(count={len(rows)})")
        _log.debug("Cached %d discovered sessions for host=%s", len(rows), host)


# singleton
session_store = SessionStore()
