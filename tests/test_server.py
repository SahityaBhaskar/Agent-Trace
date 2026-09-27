"""Integration tests for the AgentTrace HTTP server endpoints.

Run these with a live network socket.  They are skipped automatically when the
sandbox blocks outbound connections.  To run only unit tests use:

    pytest -m "not integration"

To run only integration tests use:

    pytest -m integration
"""
import json
import os
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
import pytest

# Skip all enrichment calls (Gemini) during server tests for speed
os.environ.setdefault("AGENTTRACE_SKIP_ENRICHMENT", "1")

from agent_trace.server import AgentTraceHandler, run_server  # noqa: E402

# Mark every test in this module as an integration test so it can be
# deselected with:  pytest -m "not integration"
pytestmark = pytest.mark.integration


def _start_test_server(port: int = 0) -> tuple:
    """Start a ThreadingHTTPServer on a random port. Returns (server, thread, base_url)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), AgentTraceHandler)
    server.allow_reuse_address = True
    actual_port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t, f"http://127.0.0.1:{actual_port}"


def _get(url: str) -> tuple:
    """Returns (status_code, body_dict_or_str)."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body)
            except Exception:
                return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


def _post(url: str, payload: dict) -> tuple:
    """Returns (status_code, body_dict)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Content-Length": str(len(data))},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body)
            except Exception:
                return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


class TestHealthEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.thread, cls.base = _start_test_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_health_returns_200(self):
        status, body = _get(f"{self.base}/health")
        self.assertEqual(status, 200)

    def test_health_body_has_status_ok(self):
        _, body = _get(f"{self.base}/health")
        self.assertEqual(body.get("status"), "ok")

    def test_health_body_has_version(self):
        _, body = _get(f"{self.base}/health")
        self.assertIn("version", body)


class TestScenariosEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.thread, cls.base = _start_test_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_scenarios_returns_200(self):
        status, _ = _get(f"{self.base}/api/scenarios")
        self.assertEqual(status, 200)

    def test_scenarios_returns_list_of_three(self):
        _, body = _get(f"{self.base}/api/scenarios")
        self.assertIsInstance(body, list)
        self.assertEqual(len(body), 3)

    def test_scenarios_each_has_id_and_title(self):
        _, body = _get(f"{self.base}/api/scenarios")
        for item in body:
            self.assertIn("id", item)
            self.assertIn("title", item)


class TestAskWhyEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.thread, cls.base = _start_test_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_ask_why_returns_200(self):
        status, _ = _post(f"{self.base}/api/ask-why", {
            "file_path": "src/payment.py",
            "symbol": "charge",
            "question": "Why was this changed?",
            "jev_category": "REFACTOR",
            "blast_level": "localized",
        })
        self.assertEqual(status, 200)

    def test_ask_why_has_answer_key(self):
        _, body = _post(f"{self.base}/api/ask-why", {
            "file_path": "src/payment.py",
            "symbol": "charge",
            "question": "Why was this changed?",
            "jev_category": "REFACTOR",
            "blast_level": "localized",
        })
        self.assertIn("answer", body)

    def test_ask_why_grounded_flag(self):
        _, body = _post(f"{self.base}/api/ask-why", {
            "file_path": "src/payment.py",
            "symbol": "charge",
            "question": "What breaks if I revert this?",
            "jev_category": "API_CHANGE",
            "blast_level": "service_level",
        })
        self.assertTrue(body.get("grounded"))


class TestSemanticDiffEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.thread, cls.base = _start_test_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_semantic_diff_returns_200(self):
        status, _ = _post(f"{self.base}/api/semantic-diff", {
            "file_path": "test.py",
            "before_source": "def a():\n    pass\n",
            "after_source": "def a():\n    pass\ndef b():\n    pass\n",
        })
        self.assertEqual(status, 200)

    def test_semantic_diff_has_added_key(self):
        _, body = _post(f"{self.base}/api/semantic-diff", {
            "file_path": "test.py",
            "before_source": "def a():\n    pass\n",
            "after_source": "def a():\n    pass\ndef b():\n    pass\n",
        })
        self.assertIn("added", body)
        self.assertIn("b", body["added"])

    def test_semantic_diff_has_removed_key(self):
        _, body = _post(f"{self.base}/api/semantic-diff", {
            "file_path": "test.py",
            "before_source": "def a():\n    pass\ndef b():\n    pass\n",
            "after_source": "def a():\n    pass\n",
        })
        self.assertIn("removed", body)
        self.assertIn("b", body["removed"])


class TestKnowledgeGraphEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.thread, cls.base = _start_test_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_knowledge_graph_returns_200(self):
        status, _ = _get(f"{self.base}/api/knowledge-graph")
        self.assertEqual(status, 200)

    def test_knowledge_graph_has_concepts_key(self):
        _, body = _get(f"{self.base}/api/knowledge-graph")
        self.assertIn("concepts", body)


class TestDiscoveredSessionsEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.thread, cls.base = _start_test_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_discovered_sessions_returns_200(self):
        status, body = _get(f"{self.base}/api/discovered-sessions")
        self.assertEqual(status, 200)
        self.assertIsInstance(body, list)

    def test_discovered_sessions_filter_by_repo(self):
        status, body = _get(f"{self.base}/api/discovered-sessions?repo_path=.")
        self.assertEqual(status, 200)
        self.assertIsInstance(body, list)

    def test_discovered_sessions_has_analyzed_field(self):
        status, body = _get(f"{self.base}/api/discovered-sessions")
        self.assertEqual(status, 200)
        if body:
            self.assertIn("analyzed", body[0])
            self.assertIsInstance(body[0]["analyzed"], bool)

    def test_select_session_missing_path_returns_400(self):
        status, body = _post(f"{self.base}/api/select-session", {
            "transcript_path": "/nonexistent/path/session.jsonl"
        })
        self.assertEqual(status, 400)
        self.assertIn("error", body)


class TestSessionsEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.thread, cls.base = _start_test_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_health_includes_db_enabled(self):
        status, body = _get(f"{self.base}/health")
        self.assertEqual(status, 200)
        self.assertIn("db_enabled", body)

    def test_sessions_returns_200_and_list(self):
        status, body = _get(f"{self.base}/api/sessions")
        self.assertEqual(status, 200)
        self.assertIsInstance(body, list)

    def test_sessions_with_filter_returns_200(self):
        status, body = _get(f"{self.base}/api/sessions?repo_path=.&limit=5")
        self.assertEqual(status, 200)
        self.assertIsInstance(body, list)

    def test_get_session_by_id_not_found_returns_404(self):
        status, body = _get(f"{self.base}/api/sessions/nonexistent-session-id-12345")
        self.assertEqual(status, 404)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
