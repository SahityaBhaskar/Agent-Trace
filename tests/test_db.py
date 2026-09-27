"""Unit tests for the Supabase DB client, session store, and endpoints."""
import os
import unittest
from unittest.mock import MagicMock, patch
from agent_trace.db import is_db_enabled, get_supabase_client, session_store
from agent_trace.models.report import ScenarioData, SessionStats, LogicalChange, ExecutionFlowDiff, FlowDescription, GroundedConcept
from agent_trace.models.session import SessionEvent
from agent_trace.models.graph import CausalGraphData
from agent_trace.models.jev_types import JevEvaluationResult


class TestDbClient(unittest.TestCase):
    def test_is_db_enabled_false_when_env_empty(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_ANON_KEY": ""}):
            self.assertFalse(is_db_enabled())

    def test_is_db_enabled_true_when_env_set(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "https://xyz.supabase.co", "SUPABASE_ANON_KEY": "some-key"}):
            self.assertTrue(is_db_enabled())


class TestSessionStoreStatelessFallback(unittest.TestCase):
    """When Supabase is not configured, session store methods must return safe defaults."""

    def setUp(self):
        # Ensure client returns None
        self._patcher = patch.object(session_store, "_client", return_value=None)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_list_sessions_returns_empty_list(self):
        self.assertEqual(session_store.list_sessions(), [])

    def test_get_session_returns_none(self):
        self.assertIsNone(session_store.get_session("non-existent"))

    def test_save_scenario_returns_none(self):
        dummy_scenario = MagicMock()
        self.assertIsNone(session_store.save_scenario(dummy_scenario, repo_path="."))

    def test_save_events_noop(self):
        # Should not raise exception
        session_store.save_events("test-id", [])

    def test_upsert_discovered_sessions_noop(self):
        # Should not raise exception
        session_store.upsert_discovered_sessions([])


class TestSessionStoreWithClient(unittest.TestCase):
    """Test session store methods when Supabase client is present (mocked)."""

    def setUp(self):
        self.mock_client = MagicMock()
        self._patcher = patch.object(session_store, "_client", return_value=self.mock_client)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_list_sessions_calls_table_select(self):
        mock_table = MagicMock()
        self.mock_client.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_order = MagicMock()
        mock_select.order.return_value = mock_order
        mock_limit = MagicMock()
        mock_order.limit.return_value = mock_limit
        mock_resp = MagicMock()
        mock_resp.data = [{"id": "s1", "title": "Session 1"}]
        mock_limit.execute.return_value = mock_resp

        res = session_store.list_sessions(limit=10)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "s1")
        self.mock_client.table.assert_called_with("agent_sessions")

    def test_get_session_found(self):
        mock_table = MagicMock()
        self.mock_client.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq = MagicMock()
        mock_select.eq.return_value = mock_eq
        mock_single = MagicMock()
        mock_eq.single.return_value = mock_single
        mock_resp = MagicMock()
        mock_resp.data = {"id": "s1", "payload": {}}
        mock_single.execute.return_value = mock_resp

        res = session_store.get_session("s1")
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], "s1")

    def test_save_and_get_arch_graph_l1_cache(self):
        sample_graph = {"stages": [{"id": "trigger"}], "nodes": [{"id": "n1"}], "edges": []}
        session_store.save_arch_graph("sess_arch_test_1", sample_graph)

        # Immediate retrieval from L1
        retrieved = session_store.get_arch_graph("sess_arch_test_1")
        self.assertEqual(retrieved, sample_graph)

    def test_get_arch_graph_falls_back_to_l2_supabase(self):
        mock_table = MagicMock()
        self.mock_client.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq = MagicMock()
        mock_select.eq.return_value = mock_eq
        mock_single = MagicMock()
        mock_eq.single.return_value = mock_single
        mock_eq.maybe_single.return_value = mock_single
        mock_resp = MagicMock()
        mock_resp.data = {"arch_graph": {"stages": [{"id": "abstraction"}]}}
        mock_single.execute.return_value = mock_resp

        # Call with session not in L1
        retrieved = session_store.get_arch_graph("sess_arch_l2_only")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["stages"][0]["id"], "abstraction")


if __name__ == "__main__":
    unittest.main()

