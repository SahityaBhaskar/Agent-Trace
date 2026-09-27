"""
Supabase client factory.

Reads SUPABASE_URL and SUPABASE_ANON_KEY from the environment.
Returns None (and logs a warning) when either var is missing so the
rest of the app can continue in stateless mode.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

_client = None          # cached singleton
_init_attempted = False  # only attempt once per process


def _load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
        except Exception:
            pass


def is_db_enabled() -> bool:
    """Return True only when Supabase URL and a key are present."""
    _load_env()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    return bool(os.environ.get("SUPABASE_URL") and key)


def get_supabase_client():
    """
    Return a lazily-initialised Supabase client, or None if not configured.

    Thread-safe via Python's GIL for the double-check pattern used here
    (module-level assignment is atomic in CPython).
    """
    global _client, _init_attempted

    if _init_attempted:
        return _client

    _init_attempted = True
    _load_env()

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY") or "").strip()

    if not url or not key:
        _log.info(
            "Supabase not configured (SUPABASE_URL / SUPABASE_ANON_KEY missing). "
            "Running in stateless mode — no persistence."
        )
        return None

    try:
        from supabase import create_client  # type: ignore
        _client = create_client(url, key)
        _log.info("Supabase client initialised: %s", url)
    except ImportError:
        _log.warning(
            "supabase package not installed. "
            "Run: pip install supabase>=2.0.0"
        )
        _client = None
    except Exception as exc:
        _log.error("Failed to initialise Supabase client: %s", exc)
        _client = None

    return _client
