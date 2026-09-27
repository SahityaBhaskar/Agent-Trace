"""
agent_trace.db
~~~~~~~~~~~~~~
Supabase persistence layer for AgentTrace.

Gracefully degrades when SUPABASE_URL / SUPABASE_ANON_KEY are not set —
the server continues operating in stateless mode without any errors.
"""
from .client import get_supabase_client, is_db_enabled
from .session_store import session_store

__all__ = ["get_supabase_client", "is_db_enabled", "session_store"]
