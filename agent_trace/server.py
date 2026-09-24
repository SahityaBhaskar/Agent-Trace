import os
import sys
import json
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any

from .scenarios.payment_retry import get_payment_retry_scenario
from .scenarios.auth_interceptor import get_auth_scenario
from .engine.transcript_watcher import transcript_watcher
from .engine.jev_client import jev_client
from .engine.gemini_client import gemini_client

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
DEFAULT_REPO_PATH = "."

class AgentTraceHandler(BaseHTTPRequestHandler):
    """Production-grade HTTP Request Handler for AgentTrace Control Plane."""

    protocol_version = "HTTP/1.1"

    def _set_headers(self, content_type: str = "application/json", status: int = 200, length: int = 0):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        if length > 0:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers("text/plain", 204)

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            params = parse_qs(parsed.query)

            # 1. API: List scenarios
            if path == "/api/scenarios":
                data = [
                    {"id": "payment-retry", "title": "Scenario 1: Payment Retry (3 Services Commonized)"},
                    {"id": "auth-interceptor", "title": "Scenario 2: Auth Token Refresh (Security Mutex)"},
                    {"id": "live-repo", "title": "Live Local Repository (Real Git Tree Analysis)"},
                ]
                payload = json.dumps(data).encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
                return

            # 2. API: Get Scenario by ID
            if path.startswith("/api/scenario/"):
                scenario_id = path.replace("/api/scenario/", "").strip("/")
                custom_path = params.get("path", [DEFAULT_REPO_PATH])[0]
                diff_target = params.get("diff", ["auto"])[0]

                if scenario_id == "live-repo":
                    scenario = transcript_watcher.generate_scenario_from_repo(custom_path, diff_target=diff_target)
                elif scenario_id == "auth-interceptor":
                    scenario = get_auth_scenario()
                else:
                    scenario = get_payment_retry_scenario()

                payload = scenario.model_dump_json().encode("utf-8")
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

        except Exception as e:
            err_msg = json.dumps({"error": str(e)}).encode("utf-8")
            self._set_headers("application/json", 500, len(err_msg))
            self.wfile.write(err_msg)

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
                repo_path = body.get("repo_path", DEFAULT_REPO_PATH)
                diff_target = body.get("diff_target", "auto")
                user_prompt = body.get("user_prompt", None)

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
                    user_prompt=user_prompt
                )
                payload = scenario.model_dump_json().encode("utf-8")
                self._set_headers("application/json", 200, len(payload))
                self.wfile.write(payload)
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

            msg = b"404 Not Found"
            self._set_headers("text/plain", 404, len(msg))
            self.wfile.write(msg)

        except Exception as e:
            err_msg = json.dumps({"error": str(e)}).encode("utf-8")
            self._set_headers("application/json", 500, len(err_msg))
            self.wfile.write(err_msg)

    def log_message(self, format, *args):
        sys.stderr.write(f"[AgentTrace] {self.address_string()} - {args[0]} {args[1]}\n")

def run_server(port: int = 8000, default_repo: str = "."):
    global DEFAULT_REPO_PATH
    DEFAULT_REPO_PATH = default_repo

    server_address = ("127.0.0.1", port)
    httpd = ThreadingHTTPServer(server_address, AgentTraceHandler)
    httpd.allow_reuse_address = True
    print(f"\n========================================================")
    print(f"  AgentTrace Control Plane Live: http://127.0.0.1:{port}")
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
