import os
import sys
import json
import threading
from dataclasses import dataclass
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any

from .engine.transcript_watcher import transcript_watcher
from .engine.jev_client import jev_client
from .engine.gemini_client import gemini_client
from .engine.ast_analyzer import ast_analyzer
from .engine.session_discovery import session_discovery
from .engine.architectural_impact import architectural_impact_engine
from .db import session_store, is_db_enabled

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


@dataclass
class _ServerConfig:
    """Mutable server configuration. Avoids bare module-level global mutation."""
    default_repo: str = "."


_config = _ServerConfig()


def _persist_async(fn, *args, **kwargs) -> None:
    """
    Run *fn* in a daemon thread so DB writes never block the HTTP response.
    Exceptions in the background thread are silently swallowed (the store
    already logs them at WARNING level).
    """
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()


def _save_full(scenario, repo_path: str, diff_target: str = "auto", transcript_path: str = None):
    """
    Background task: persist scenario, events, AND architectural graph.
    Runs in a daemon thread — never blocks HTTP responses.
    """
    import logging
    _log = logging.getLogger(__name__)
    try:
        # 1. Persist scenario row
        saved_id = session_store.save_scenario(
            scenario=scenario,
            repo_path=repo_path,
            diff_target=diff_target,
            transcript_path=transcript_path,
        )
        effective_id = saved_id or scenario.id
        # 2. Persist events
        if hasattr(scenario, "events") and scenario.events:
            session_store.save_events(effective_id, scenario.events)
        # 3. Build and cache architectural graph
        try:
            arch = architectural_impact_engine.build_from_scenario(scenario)
            session_store.save_arch_graph(effective_id, arch.model_dump())
            _log.debug("Arch graph built and cached for session %s", effective_id)
        except Exception as arch_exc:
            _log.warning("Arch graph build failed for %s: %s", effective_id, arch_exc)
    except Exception as exc:
        _log.warning("Failed saving session %s: %s", getattr(scenario, "id", "?"), exc)


class AgentTraceServer(ThreadingHTTPServer):
    """Custom ThreadingHTTPServer that suppresses benign client disconnect tracebacks."""
    def handle_error(self, request, client_address):
        # Ignore client disconnect errors (Errno 54 Connection reset, Errno 32 Broken pipe)
        exctype, value, tb = sys.exc_info()
        if exctype in (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            return
        super().handle_error(request, client_address)


class AgentTraceHandler(BaseHTTPRequestHandler):
    """Production-grade HTTP Request Handler for AgentTrace Control Plane."""

    protocol_version = "HTTP/1.1"

    def handle_one_request(self):
        """Override to silently ignore connection resets while reading request."""
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            self.close_connection = True

    def _set_headers(self, content_type: str = "application/json", status: int = 200, length: int = 0):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            if length > 0:
                self.send_header("Content-Length", str(length))
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def _safe_write(self, data: bytes):
        """Safely writes bytes to the client socket, silently handling client disconnects."""
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def do_OPTIONS(self):
        self._set_headers("text/plain", 204)

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            params = parse_qs(parsed.query)


            # 1. API: List scenarios (live-repo only — demos removed)
            if path == "/api/scenarios":
                data = [
                    {"id": "live-repo", "title": "Live Local Repository (Real Git Tree Analysis)"},
                ]
                payload = json.dumps(data).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # 2. API: Get Scenario by ID (live-repo only)
            if path.startswith("/api/scenario/"):
                scenario_id = path.replace("/api/scenario/", "").strip("/")
                custom_path = params.get("path", [_config.default_repo])[0]
                diff_target = params.get("diff", ["auto"])[0]

                scenario = transcript_watcher.generate_scenario_from_repo(custom_path, diff_target=diff_target)
                payload = scenario.model_dump_json().encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return


            # Health check endpoint
            if path == "/health":
                import datetime
                health = {
                    "status": "ok",
                    "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "version": "1.0.0",
                    "db_enabled": is_db_enabled(),
                }
                payload = json.dumps(health).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # DB: List stored sessions (GET /api/sessions?repo_path=<path>&limit=50)
            if path == "/api/sessions":
                repo_path = params.get("repo_path", [None])[0]
                limit     = int(params.get("limit", ["50"])[0])
                sessions  = session_store.list_sessions(repo_path=repo_path, limit=limit)
                payload   = json.dumps(sessions, default=str).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # DB: Retrieve a single stored session by ID (GET /api/sessions/<id>)
            if path.startswith("/api/sessions/"):
                session_id = path.replace("/api/sessions/", "").strip("/")
                session    = session_store.get_session(session_id)
                if session is None:
                    err = json.dumps({"error": f"Session '{session_id}' not found."}).encode("utf-8")
                    self._set_headers("application/json", 404, len(err))
                    self.wfile.write(err)
                    return
                payload = json.dumps(session, default=str).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # 3. API: Jev connection status
            if path == "/api/jev/status":
                status = jev_client.test_connection()
                payload = json.dumps(status).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # Developer Knowledge Graph
            if path == "/api/knowledge-graph":
                from .engine.knowledge_graph import get_knowledge_graph
                payload = json.dumps(get_knowledge_graph()).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # Semantic Impact Graph (GET /api/impact-graph?path=.&diff=auto)
            # Optional: &session_id=<id> (for display context only, not cached here)
            # Optional: &chunk_index=N or &chunk_id=<chunk_id> for per-chunk subgraph
            if path == "/api/impact-graph":
                custom_path = params.get("path", [_config.default_repo])[0]
                diff_target = params.get("diff", ["auto"])[0]
                max_depth = int(params.get("depth", ["2"])[0])

                # Per-chunk subgraph selectors (both optional)
                chunk_id_param = params.get("chunk_id", [None])[0]
                chunk_index_param = params.get("chunk_index", [None])[0]
                chunk_index_int: "int | None" = int(chunk_index_param) if chunk_index_param is not None else None

                from .engine.semantic_impact import get_impact_graph_builder
                from .engine.live_git import live_git_engine as _lg
                from pathlib import Path as _Path

                _lg.repo_path = _Path(custom_path).expanduser().resolve()
                meta = _lg.get_repo_meta()
                raw_diff, _ = _lg.get_diff(diff_target)
                hunks = _lg.parse_diff_hunks(raw_diff)

                builder = get_impact_graph_builder(
                    repo_path=str(_lg.repo_path),
                    max_depth=max_depth,
                )
                graph = builder.build(
                    hunks=hunks,
                    change_set_id=meta.get("commit_hash", "live"),
                    repository=meta.get("name", "repo"),
                )

                # If a chunk selector was provided, narrow down to that chunk's subgraph
                if chunk_id_param is not None or chunk_index_int is not None:
                    subgraph = graph.get_chunk_subgraph(
                        chunk_id=chunk_id_param,
                        chunk_index=chunk_index_int,
                    )
                    if subgraph is None:
                        err = b'{"error": "chunk not found"}'
                        self._set_headers("application/json", 404, len(err))
                        self.wfile.write(err)
                        return
                    graph = subgraph

                payload = graph.model_dump_json().encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # Architectural Impact Flow
            # GET /api/architectural-impact?session_id=<id>&path=.&diff=auto[&prompt=...]
            #
            # Cache strategy (L1 → L2 → live build):
            #   1. If session_id given → check LRU / Supabase (instant if warm)
            #   2. Cache miss → build from live git diff
            #   3. Async-save result back to cache keyed by session_id (if provided)
            if path == "/api/architectural-impact":
                session_id  = params.get("session_id", [None])[0]
                custom_path = params.get("path", [_config.default_repo])[0]
                diff_target = params.get("diff", ["auto"])[0]
                user_prompt = params.get("prompt", [None])[0]

                # ── 1. Cache hit (L1 LRU / L2 Supabase) ────────────────────────
                if session_id:
                    cached = session_store.get_arch_graph(session_id)
                    if cached:
                        payload = json.dumps(cached).encode("utf-8")
                        self._set_headers("application/json", 200, len(payload))
                        self.wfile.write(payload)
                        return

                    # Fast path: If session already exists in DB, build from its stored scenario payload instantly
                    stored_session = session_store.get_session(session_id)
                    if stored_session and "payload" in stored_session and stored_session["payload"]:
                        try:
                            from .models.report import ScenarioData
                            sc_payload = stored_session["payload"]
                            sc_obj = ScenarioData.model_validate(sc_payload if isinstance(sc_payload, dict) else json.loads(sc_payload))
                            arch_graph = architectural_impact_engine.build_from_scenario(sc_obj)
                            arch_dict = arch_graph.model_dump()
                            _persist_async(session_store.save_arch_graph, session_id, arch_dict)
                            payload = json.dumps(arch_dict).encode("utf-8")
                            self._set_headers("application/json", 200, len(payload))
                            self.wfile.write(payload)
                            return
                        except Exception:
                            pass

                # ── 2. Cache miss → lightweight live build ───────────────────
                from .engine.live_git import live_git_engine as _lg
                from .engine.semantic_impact import get_impact_graph_builder
                from pathlib import Path as _Path

                _lg.repo_path = _Path(custom_path).expanduser().resolve()
                meta = _lg.get_repo_meta()
                raw_diff, _ = _lg.get_diff(diff_target)
                hunks = _lg.parse_diff_hunks(raw_diff)

                # Fast scan: max_depth=1 is sufficient for immediate caller & omission detection
                builder = get_impact_graph_builder(
                    repo_path=str(_lg.repo_path),
                    max_depth=1,
                )
                sig = builder.build(
                    hunks=hunks,
                    change_set_id=meta.get("commit_hash", "live"),
                    repository=meta.get("name", "repo"),
                )
                arch_graph = architectural_impact_engine.build_from_live_diff(
                    hunks=hunks,
                    impact_graph=sig,
                    user_prompt=user_prompt,
                )


                arch_dict = arch_graph.model_dump()

                # ── 3. Async cache save (non-blocking) ───────────────────────
                if session_id:
                    _persist_async(session_store.save_arch_graph, session_id, arch_dict)

                payload = json.dumps(arch_dict).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return


            # 5. Discovered AI agent sessions (auto-discovery)
            # GET /api/discovered-sessions?repo_path=<path>&max_age_days=30
            if path == "/api/discovered-sessions":
                repo_path = params.get("repo_path", [_config.default_repo])[0]
                max_age_days = int(params.get("max_age_days", ["30"])[0])
                sessions = session_discovery.discover_all(
                    repo_path=repo_path,
                    max_age_days=max_age_days,
                )
                if sessions:
                    _persist_async(session_store.upsert_discovered_sessions, sessions)

                analyzed_map = session_store.get_all_analyzed_transcripts_map()
                session_dicts = []
                for s in sessions:
                    d = s.to_dict()
                    info = analyzed_map.get(s.transcript_path) if s.transcript_path else None
                    if info:
                        d["analyzed"] = True
                        d["cached_session_id"] = info.get("id")
                        d["cached_at"] = info.get("created_at")
                        d["stats"] = info.get("stats")
                    else:
                        d["analyzed"] = False
                        d["cached_session_id"] = None
                        d["cached_at"] = None
                    session_dicts.append(d)

                payload = json.dumps(session_dicts).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # 6. Check whether a transcript has already been analyzed
            # GET /api/check-session?transcript_path=<path>
            # Returns { analyzed: bool, session_id: str|null, created_at: str|null }
            if path == "/api/check-session":
                transcript_path = params.get("transcript_path", [""])[0]
                cached = session_store.get_session_by_transcript(transcript_path) if transcript_path else None
                payload = json.dumps({
                    "analyzed": cached is not None,
                    "session_id": cached["id"] if cached else None,
                    "created_at": cached.get("created_at") if cached else None,
                }).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # 4. Static Dashboard: Serve index.html

            html_file = STATIC_DIR / "index.html"
            if html_file.exists():
                with open(html_file, "rb") as f:
                    content = f.read()
                self._set_headers("text/html; charset=utf-8", 200, len(content))
                self.wfile.write(content)
                return

            # 404 Fallback
            msg = b"404 Not Found"
            self._set_headers("text/plain", 404, len(msg))
            self.wfile.write(msg)

        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as e:
            err_msg = json.dumps({"error": str(e)}).encode("utf-8")
            self._set_headers("application/json", 500, len(err_msg))
            self._safe_write(err_msg)

    def do_POST(self):
        try:
            # Always consume body first to prevent socket desynchronization in HTTP keep-alive
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length) if content_length > 0 else b""
            body = json.loads(post_data.decode("utf-8")) if post_data else {}

            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            # Dynamic Live Repository Analysis Endpoint
            if path == "/api/analyze-repo":
                repo_path = body.get("repo_path", _config.default_repo)
                diff_target = body.get("diff_target", "auto")
                user_prompt = body.get("user_prompt", None)
                transcript_path = body.get("transcript_path", None)

                resolved_path = Path(repo_path).expanduser().resolve()
                from .engine.live_git import live_git_engine
                live_git_engine.repo_path = resolved_path
                if not live_git_engine.is_git_repo():
                    err = json.dumps({"error": f"Path '{repo_path}' is not an accessible git repository. Check path and permissions."}).encode("utf-8")
                    self._set_headers("application/json", 400, len(err))
                    self.wfile.write(err)
                    return

                scenario = transcript_watcher.generate_scenario_from_repo(
                    repo_path=str(resolved_path),
                    diff_target=diff_target,
                    user_prompt=user_prompt,
                    transcript_path=transcript_path,
                )
                _persist_async(
                    _save_full,
                    scenario,
                    repo_path=str(resolved_path),
                    diff_target=diff_target,
                    transcript_path=transcript_path,
                )
                payload = scenario.model_dump_json().encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # Select a specific discovered session
            # POST /api/select-session
            # body: { transcript_path: str, repo_path: str, diff_target: str }
            if path == "/api/select-session":
                transcript_path = body.get("transcript_path", "")
                repo_path = body.get("repo_path") or _config.default_repo
                diff_target = body.get("diff_target", "auto")

                if not transcript_path or not Path(transcript_path).exists():
                    err = json.dumps({"error": f"Transcript path does not exist: {transcript_path}"}).encode("utf-8")
                    self._set_headers("application/json", 400, len(err))
                    self.wfile.write(err)
                    return

                resolved_repo = Path(repo_path).expanduser().resolve()
                scenario = transcript_watcher.generate_scenario_from_repo(
                    repo_path=str(resolved_repo),
                    diff_target=diff_target,
                    transcript_path=transcript_path,
                )
                _persist_async(
                    _save_full,
                    scenario,
                    repo_path=str(resolved_repo),
                    diff_target=diff_target,
                    transcript_path=transcript_path,
                )
                payload = scenario.model_dump_json().encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # Auto-pick the most relevant active session
            # POST /api/auto-pick-session
            # body: { repo_path: str, diff_target: str }
            if path == "/api/auto-pick-session":
                repo_path = body.get("repo_path") or _config.default_repo
                diff_target = body.get("diff_target", "auto")
                picked = session_discovery.auto_pick(repo_path)
                if not picked:
                    err = json.dumps({"error": "No matching or active AI agent session found on the system."}).encode("utf-8")
                    self._set_headers("application/json", 404, len(err))
                    self.wfile.write(err)
                    return

                resolved_repo = Path(repo_path).expanduser().resolve()
                scenario = transcript_watcher.generate_scenario_from_repo(
                    repo_path=str(resolved_repo),
                    diff_target=diff_target,
                    transcript_path=picked.transcript_path,
                )
                _persist_async(
                    _save_full,
                    scenario,
                    repo_path=str(resolved_repo),
                    diff_target=diff_target,
                    transcript_path=picked.transcript_path,
                )
                payload = scenario.model_dump_json().encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # Smart analyze-session: checks DB cache first, runs fresh analysis if not found
            # POST /api/analyze-session
            # body: { session_id, transcript_path, repo_path, git_root, diff_target }
            if path == "/api/analyze-session":
                transcript_path = body.get("transcript_path", "")
                repo_path       = body.get("repo_path") or body.get("git_root") or _config.default_repo
                diff_target     = body.get("diff_target", "auto")

                # ── 1. Check DB cache ────────────────────────────────────────
                if transcript_path:
                    cached_meta = session_store.get_session_by_transcript(transcript_path)
                    if cached_meta:
                        # We have a prior analysis — return the full payload from DB
                        full = session_store.get_session(cached_meta["id"])
                        if full and full.get("payload"):
                            payload_bytes = json.dumps(
                                {"cached": True, "scenario": full["payload"]}
                            ).encode("utf-8")
                            self._set_headers("application/json", 200, len(payload_bytes))
                            self.wfile.write(payload_bytes)
                            return

                # ── 2. Validate repo path ────────────────────────────────────
                resolved_repo = Path(repo_path).expanduser().resolve()
                from .engine.live_git import live_git_engine
                live_git_engine.repo_path = resolved_repo
                if not live_git_engine.is_git_repo():
                    err = json.dumps({
                        "error": f"'{repo_path}' is not an accessible git repository."
                    }).encode("utf-8")
                    self._set_headers("application/json", 400, len(err))
                    self.wfile.write(err)
                    return

                # ── 3. Run fresh analysis ────────────────────────────────────
                scenario = transcript_watcher.generate_scenario_from_repo(
                    repo_path=str(resolved_repo),
                    diff_target=diff_target,
                    transcript_path=transcript_path or None,
                )
                if transcript_path:
                    import hashlib
                    scenario.id = f"session_{hashlib.sha256(transcript_path.encode()).hexdigest()[:12]}"
                _persist_async(
                    _save_full,
                    scenario,
                    repo_path=str(resolved_repo),
                    diff_target=diff_target,
                    transcript_path=transcript_path or None,
                )
                payload_bytes = json.dumps(
                    {"cached": False, "scenario": json.loads(scenario.model_dump_json())}
                ).encode("utf-8")
                self._set_headers("application/json", 200, len(payload_bytes))
                self.wfile.write(payload_bytes)
                return

            # Ask Why grounded endpoint
            if path == "/api/ask-why":

                file_path = body.get("file_path", "")
                symbol = body.get("symbol", "")
                question = body.get("question", "Why was this changed?")
                evidence = body.get("evidence", "")
                diff = body.get("diff", "")
                jev_category = body.get("jev_category", "REFACTOR")
                blast_level = body.get("blast_level", "localized")

                result = gemini_client.answer_grounded_question(
                    file_path=file_path,
                    symbol=symbol,
                    question=question,
                    evidence_summary=evidence,
                    diff_snippet=diff,
                    jev_category=jev_category,
                    blast_level=blast_level,
                )

                payload = json.dumps(result).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # On-demand learning enrichment
            if path == "/api/learning/enrich":
                concept      = body.get("concept", {})
                changes      = body.get("logical_changes", [])
                user_prompt  = body.get("user_prompt", "")

                meta_dict = gemini_client.enrich_learning_concept(
                    concept_name=concept.get("name", ""),
                    concept_what=concept.get("what_it_is", ""),
                    concept_how=concept.get("how_your_repo_uses_it", ""),
                    logical_changes_summary="; ".join(
                        f"{ch.get('title','')} ({ch.get('category','')})" for ch in changes
                    ),
                    user_prompt=user_prompt,
                )
                lessons_list = gemini_client.enrich_change_lessons(
                    logical_changes=changes,
                    user_prompt=user_prompt,
                )
                meta_dict["change_lessons"] = lessons_list
                payload = json.dumps(meta_dict).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # AST Semantic Diff endpoint
            if path == "/api/semantic-diff":
                file_path = body.get("file_path", "unknown.py")
                before_source = body.get("before_source", "")
                after_source = body.get("after_source", "")

            # Risk Deep Analysis endpoint
            if path == "/api/risk/analyze":
                rule = body.get("rule", "Risk Review")
                symbol = body.get("symbol", "")
                file_path = body.get("file_path", "")
                diff_snippet = body.get("diff_snippet", "")
                jev_category = body.get("jev_category", "REFACTOR")
                blast_level = body.get("blast_level", "localized")
                blast_score = float(body.get("blast_score", 1.0))
                human_review_probability = float(body.get("human_review_probability", 0.8))
                breaking_risk = bool(body.get("breaking_risk", False))
                omission_rationale = body.get("omission_rationale", "")
                graph_context = body.get("graph_context", {})

                analysis = gemini_client.synthesize_deep_risk_analysis(
                    rule=rule,
                    symbol=symbol,
                    file_path=file_path,
                    diff_snippet=diff_snippet,
                    jev_category=jev_category,
                    blast_level=blast_level,
                    blast_score=blast_score,
                    human_review_probability=human_review_probability,
                    breaking_risk=breaking_risk,
                    omission_rationale=omission_rationale,
                    graph_context=graph_context,
                )
                payload = json.dumps(analysis).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            msg = b"404 Not Found"
            self._set_headers("text/plain", 404, len(msg))
            self.wfile.write(msg)

        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as e:
            err_msg = json.dumps({"error": str(e)}).encode("utf-8")
            self._set_headers("application/json", 500, len(err_msg))
            self._safe_write(err_msg)

    def log_message(self, format, *args):
        import datetime
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        code = args[1] if len(args) > 1 else "-"
        method_path = args[0] if args else "-"
        sys.stderr.write(
            f'{{"time":"{ts}","remote":"{self.address_string()}","request":"{method_path}","status":"{code}"}}\n'
        )

def _make_wsgi_app():
    """
    Thin WSGI shim that bridges AgentTraceHandler (BaseHTTPRequestHandler) to the
    WSGI interface expected by waitress.  Each request is dispatched through the
    handler's do_GET / do_POST methods by constructing a minimal fake socket that
    reads from a BytesIO and writes back to a BytesIO, then the response bytes are
    returned to waitress.

    This avoids replacing the entire server with a WSGI framework while still
    allowing waitress to front the HTTP/1.1 keep-alive and threading.
    """
    import io
    from http.server import BaseHTTPRequestHandler
    from socketserver import BaseRequestHandler

    class _FakeSocket:
        """A BytesIO-backed fake socket that satisfies BaseHTTPRequestHandler's makefile()."""
        def __init__(self, request_bytes: bytes):
            self._rbuf = io.BytesIO(request_bytes)
            self._wbuf = io.BytesIO()

        def makefile(self, mode: str, *args, **kwargs):
            if "r" in mode:
                return io.BufferedReader(self._rbuf)  # type: ignore[arg-type]
            return self._wbuf

        def getsockname(self):
            return ("127.0.0.1", 8000)

        def getpeername(self):
            return ("127.0.0.1", 0)

        def sendall(self, data):
            self._wbuf.write(data)

    def _wsgi_app(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        qs = environ.get("QUERY_STRING", "")
        full_path = path + ("?" + qs if qs else "")

        content_length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(content_length) if content_length else b""

        # Build a minimal raw HTTP request that BaseHTTPRequestHandler can parse
        request_line = f"{method} {full_path} HTTP/1.1\r\n"
        headers = f"Host: {environ.get('HTTP_HOST', 'localhost')}\r\n"
        if body:
            headers += f"Content-Length: {len(body)}\r\n"
            ct = environ.get("CONTENT_TYPE", "")
            if ct:
                headers += f"Content-Type: {ct}\r\n"
        raw = (request_line + headers + "\r\n").encode() + body

        sock = _FakeSocket(raw)
        client_addr = ("127.0.0.1", 0)

        # BaseHTTPRequestHandler.__init__ calls handle() which calls handle_one_request()
        handler = AgentTraceHandler.__new__(AgentTraceHandler)
        BaseRequestHandler.__init__(handler, sock, client_addr, None)  # type: ignore[arg-type]

        # Collect the raw response written to the fake socket
        sock._wbuf.seek(0)
        raw_response = sock._wbuf.read()

        if not raw_response:
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"Empty response from handler"]

        # Parse the raw HTTP response: split status line + headers from body
        try:
            header_part, _, body_part = raw_response.partition(b"\r\n\r\n")
            lines = header_part.decode("latin-1").split("\r\n")
            status_line = lines[0]          # e.g. "HTTP/1.1 200 OK"
            status_code = status_line.split(" ", 2)[1]
            reason = status_line.split(" ", 2)[2] if len(status_line.split(" ", 2)) > 2 else "OK"
            wsgi_status = f"{status_code} {reason}"

            response_headers = []
            for hdr in lines[1:]:
                if ":" in hdr:
                    name, _, value = hdr.partition(":")
                    response_headers.append((name.strip(), value.strip()))

            start_response(wsgi_status, response_headers)
            return [body_part]
        except Exception as exc:
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [f"Response parse error: {exc}".encode()]

    return _wsgi_app


def run_server(port: int = 8000, default_repo: str = "."):
    _config.default_repo = default_repo

    bind_host = os.environ.get("AGENTTRACE_HOST", "127.0.0.1")
    server_address = (bind_host, port)

    # Try waitress for production-grade serving; fall back to ThreadingHTTPServer
    try:
        import waitress as _waitress
        print(f"\n========================================================")
        print(f"  AgentTrace Control Plane Live: http://127.0.0.1:{port}")
        print(f"  Target Repository: {Path(default_repo).resolve()}")
        print(f"  Production Server: waitress")
        print(f"  Press Ctrl+C to stop")
        print(f"========================================================\n")
        _waitress.serve(_make_wsgi_app(), host=bind_host, port=port)
        return
    except ImportError:
        pass
    httpd = AgentTraceServer(server_address, AgentTraceHandler)
    httpd.allow_reuse_address = True
    print(f"\n========================================================")
    print(f"  AgentTrace Control Plane Live: http://{bind_host}:{port}")
    print(f"  Target Repository: {Path(default_repo).resolve()}")
    print(f"  Threaded Server Active (Concurrent Request Support)")
    print(f"  Press Ctrl+C to stop")
    print(f"========================================================\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down AgentTrace server...")
        httpd.shutdown()
        httpd.server_close()
