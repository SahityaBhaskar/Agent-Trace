"""Unit tests for CausalEngine.build_causal_graph."""
import unittest
from agent_trace.engine.causal_engine import CausalEngine
from agent_trace.models.session import SessionEvent, EventTarget, EventPayload
from agent_trace.models.report import (
    LogicalChange, DiffHunk, AttentionItem, GroundedConcept,
)
from agent_trace.models.jev_types import (
    JevEvaluationResult, JevClassificationResult,
    JevBlastRadiusResult, JevHumanReviewResult, JevBreakingRiskResult,
)


def _make_hunk(hunk_id: str = "h1", file_path: str = "src/api.py") -> DiffHunk:
    jev = JevEvaluationResult(
        hunk_id=hunk_id,
        classification=JevClassificationResult(category="REFACTOR", confidence=0.9, choice="REFACTOR"),
        blast_radius=JevBlastRadiusResult(score=2.2, level="localized"),
        human_review=JevHumanReviewResult(required=False, probability=0.4),
        breaking_risk=JevBreakingRiskResult(is_breaking=False, probability=0.1),
        latency_ms=5,
        source="calibrated_cache",
    )
    return DiffHunk(
        id=hunk_id, file_path=file_path, symbol="my_func",
        old_lines="-old", new_lines="+new", line_range="1-5",
        change_type="MODIFIED", jev_result=jev,
    )


def _make_change(hunks=None) -> LogicalChange:
    return LogicalChange(
        id="change_1", title="Test change", category="REFACTOR",
        why_explanation="Testing causal graph.", how_arrived_steps=["step1"],
        epistemic_status="OBSERVED", confidence_score=0.9,
        hunks=hunks or [_make_hunk()],
        affected_services=["src/api.py"], blast_radius="localized",
        review_checklist=[],
    )


def _make_event(action_type: str, seq: int = 1) -> SessionEvent:
    return SessionEvent(
        event_id=f"evt_{seq}",
        session_id="sess_1",
        sequence_number=seq,
        timestamp="00:00:01",
        agent_id="test_agent",
        action_type=action_type,
        epistemic_status="OBSERVED",
        target=EventTarget(file_path="src/api.py", query="retry"),
        payload=EventPayload(output_summary="observed action"),
    )


class TestCausalEngineNoEvents(unittest.TestCase):
    """Live-repo mode: no transcript events, synthesize git+AST investigation node."""

    def setUp(self):
        self.engine = CausalEngine()
        self.change = _make_change()

    def test_graph_has_user_request_node(self):
        graph = self.engine.build_causal_graph("Add retry logic", [], [self.change])
        node_types = [n.type for n in graph.nodes]
        self.assertIn("USER_REQUEST", node_types)

    def test_graph_has_investigation_node_for_no_events(self):
        graph = self.engine.build_causal_graph("Add retry logic", [], [self.change])
        node_types = [n.type for n in graph.nodes]
        self.assertIn("AGENT_INVESTIGATION", node_types)

    def test_graph_has_architectural_decision_node(self):
        graph = self.engine.build_causal_graph("Add retry logic", [], [self.change])
        node_types = [n.type for n in graph.nodes]
        self.assertIn("ARCHITECTURAL_DECISION", node_types)

    def test_graph_has_code_hunk_node(self):
        graph = self.engine.build_causal_graph("Add retry logic", [], [self.change])
        node_types = [n.type for n in graph.nodes]
        self.assertIn("CODE_HUNK", node_types)

    def test_edges_not_empty(self):
        graph = self.engine.build_causal_graph("Add retry logic", [], [self.change])
        self.assertGreater(len(graph.edges), 0)

    def test_node_title_uses_file_name_not_symbol(self):
        graph = self.engine.build_causal_graph("Add retry logic", [], [self.change])
        file_node = next(n for n in graph.nodes if n.type in ("CODE_HUNK", "LOGICAL_CHANGE"))
        self.assertEqual(file_node.title, "api.py")
        self.assertIn("hunk", file_node.subtitle)


class TestCausalEngineFileGrouping(unittest.TestCase):
    """Test grouping code hunks by file rather than symbols."""

    def setUp(self):
        self.engine = CausalEngine()

    def test_multiple_hunks_same_file_grouped_into_single_node(self):
        h1 = _make_hunk("h1", "src/api.py")
        h2 = _make_hunk("h2", "src/api.py")
        h2.symbol = "other_func"
        change = _make_change(hunks=[h1, h2])

        graph = self.engine.build_causal_graph("Prompt", [], [change])
        file_nodes = [n for n in graph.nodes if n.type in ("CODE_HUNK", "LOGICAL_CHANGE")]
        self.assertEqual(len(file_nodes), 1)
        self.assertEqual(file_nodes[0].title, "api.py")
        self.assertEqual(file_nodes[0].data["hunk_count"], 2)
        self.assertIn("my_func", file_nodes[0].data["symbols"])
        self.assertIn("other_func", file_nodes[0].data["symbols"])

    def test_different_files_produce_separate_file_nodes(self):
        h1 = _make_hunk("h1", "src/api.py")
        h2 = _make_hunk("h2", "src/db.py")
        change = _make_change(hunks=[h1, h2])

        graph = self.engine.build_causal_graph("Prompt", [], [change])
        file_nodes = [n for n in graph.nodes if n.type in ("CODE_HUNK", "LOGICAL_CHANGE")]
        self.assertEqual(len(file_nodes), 2)
        titles = {n.title for n in file_nodes}
        self.assertEqual(titles, {"api.py", "db.py"})


class TestCausalEngineWithEvents(unittest.TestCase):
    """Transcript mode: events present, investigation nodes derived from them."""

    def setUp(self):
        self.engine = CausalEngine()
        self.change = _make_change()
        self.events = [
            _make_event("FILE_READ", seq=1),
            _make_event("REPOSITORY_SEARCH", seq=2),
            _make_event("COMMAND_EXECUTED", seq=3),  # should NOT produce investigation node
        ]

    def test_file_read_event_creates_investigation_node(self):
        graph = self.engine.build_causal_graph("Fix bug", self.events, [self.change])
        inv_nodes = [n for n in graph.nodes if n.type == "AGENT_INVESTIGATION"]
        # FILE_READ + REPOSITORY_SEARCH → 2 investigation nodes
        self.assertGreaterEqual(len(inv_nodes), 2)

    def test_command_executed_does_not_create_investigation_node(self):
        only_cmd_events = [_make_event("COMMAND_EXECUTED", seq=1)]
        graph = self.engine.build_causal_graph("Fix bug", only_cmd_events, [self.change])
        inv_nodes = [n for n in graph.nodes if n.type == "AGENT_INVESTIGATION"]
        # When events are present but none are investigation types,
        # CausalEngine does not synthesize a fallback node — no investigation nodes expected.
        self.assertEqual(len(inv_nodes), 0)


class TestCausalEngineAttentionItems(unittest.TestCase):

    def setUp(self):
        self.engine = CausalEngine()
        self.change = _make_change()
        self.attention = [
            AttentionItem(
                title="High blast", level="HIGH", detail="risk",
                action_required="test", jev_attention_probability=0.9,
            )
        ]

    def test_risk_flag_node_created(self):
        graph = self.engine.build_causal_graph("prompt", [], [self.change], attention_items=self.attention)
        risk_nodes = [n for n in graph.nodes if n.type == "RISK_FLAG"]
        self.assertEqual(len(risk_nodes), 1)

    def test_risk_node_title_matches_attention_item(self):
        graph = self.engine.build_causal_graph("prompt", [], [self.change], attention_items=self.attention)
        risk_node = next(n for n in graph.nodes if n.type == "RISK_FLAG")
        self.assertIn("High blast", risk_node.title)


class TestCausalEngineGroundedConcept(unittest.TestCase):

    def setUp(self):
        self.engine = CausalEngine()
        self.change = _make_change()
        self.concept = GroundedConcept(
            name="Retry Pattern",
            headline="Centralised retry logic",
            what_it_is="Retry abstraction",
            how_your_repo_uses_it="RetryExecutor used by 3 services",
            code_snippet="# RetryExecutor",
            pitfalls_to_watch=["idempotency"],
            related_concepts=["Circuit Breaker"],
        )

    def test_learning_concept_node_created(self):
        graph = self.engine.build_causal_graph(
            "prompt", [], [self.change], grounded_concept=self.concept
        )
        concept_nodes = [n for n in graph.nodes if n.type == "LEARNING_CONCEPT"]
        self.assertEqual(len(concept_nodes), 1)

    def test_learning_concept_node_title_contains_concept_name(self):
        graph = self.engine.build_causal_graph(
            "prompt", [], [self.change], grounded_concept=self.concept
        )
        concept_node = next(n for n in graph.nodes if n.type == "LEARNING_CONCEPT")
        self.assertIn("Retry Pattern", concept_node.title)


class TestCausalEngineReasoningPreservation(unittest.TestCase):
    def setUp(self):
        self.engine = CausalEngine()
        self.change = _make_change()

    def test_reasoning_before_investigation_node_is_preserved_in_full(self):
        long_reasoning = "Detailed explanation of why we are investigating the payment processor: " + ("X" * 300)
        events = [
            SessionEvent(
                event_id="evt_think_1",
                session_id="sess_1",
                sequence_number=1,
                timestamp="00:00:01",
                agent_id="test_agent",
                action_type="AGENT_REASONING_NOTE",
                epistemic_status="DECLARED",
                target=EventTarget(query=long_reasoning),
                payload=EventPayload(output_summary=long_reasoning),
            ),
            SessionEvent(
                event_id="evt_read_1",
                session_id="sess_1",
                sequence_number=2,
                timestamp="00:00:02",
                agent_id="test_agent",
                action_type="FILE_READ",
                epistemic_status="OBSERVED",
                target=EventTarget(file_path="src/payment_gateway.py"),
                payload=EventPayload(output_summary="Read payment_gateway.py"),
            ),
        ]
        graph = self.engine.build_causal_graph("Fix payment", events, [self.change])
        inv_node = next(n for n in graph.nodes if n.type == "AGENT_INVESTIGATION")
        self.assertEqual(inv_node.data.get("reasoning"), long_reasoning)
        self.assertEqual(inv_node.data.get("reasoning_epistemic"), "DECLARED")


if __name__ == "__main__":
    unittest.main()
