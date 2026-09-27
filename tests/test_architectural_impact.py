"""
Unit tests for the Architectural Impact Flow Engine, AI synthesis, and DAG models.
"""

import pytest
from unittest.mock import MagicMock

from agent_trace.engine.architectural_impact import (
    ArchitecturalImpactEngine,
    ArchitecturalGraphResponse,
    ArchitecturalNode,
    ArchitecturalEdge,
    architectural_impact_engine,
)
from agent_trace.engine.gemini_client import GeminiClient
from agent_trace.models.report import (
    ScenarioData,
    LogicalChange,
    DiffHunk,
    AttentionItem,
    ReviewChecklistItem,
    ExecutionFlowDiff,
    FlowDescription,
    SessionStats,
    GroundedConcept,
)
from agent_trace.models.impact_graph import SemanticImpactGraph, ImpactNode, ImpactEdge


def _make_hunk(
    hunk_id: str,
    file_path: str,
    symbol: str = "func",
    change_type: str = "MODIFIED",
    line_range: str = "Lines 1-10",
    old_lines: str = "- pass",
    new_lines: str = "+ return True",
) -> DiffHunk:
    from agent_trace.models.jev_types import (
        JevEvaluationResult,
        JevClassificationResult,
        JevBlastRadiusResult,
        JevHumanReviewResult,
        JevBreakingRiskResult,
    )
    return DiffHunk(
        id=hunk_id,
        file_path=file_path,
        symbol=symbol,
        line_range=line_range,
        change_type=change_type,
        old_lines=old_lines,
        new_lines=new_lines,
        jev_result=JevEvaluationResult(
            hunk_id=hunk_id,
            classification=JevClassificationResult(category="REFACTOR", confidence=0.9, choice="REFACTOR"),
            blast_radius=JevBlastRadiusResult(score=1.5, level="localized"),
            human_review=JevHumanReviewResult(required=False, probability=0.1),
            breaking_risk=JevBreakingRiskResult(is_breaking=False, probability=0.1),
            latency_ms=10,
            source="calibrated_cache",
        ),
    )


def _make_scenario(
    scenario_id: str = "live-session-1",
    user_prompt: str = "Add commonized retry executor to payment flow",
    hunks: list = None,
    attention_items: list = None,
) -> ScenarioData:
    if hunks is None:
        hunks = [
            _make_hunk("h1", "src/core/RetryExecutor.py", symbol="RetryExecutor", change_type="ADDED"),
            _make_hunk("h2", "src/services/PaymentService.py", symbol="charge", change_type="MODIFIED"),
        ]
    if attention_items is None:
        attention_items = [
            AttentionItem(
                title="Unattended caller in RefundService",
                level="HIGH",
                detail="RefundService.refund still calls legacy endpoint",
                action_required="Update to RetryExecutor",
                jev_attention_probability=0.88,
            )
        ]
    return ScenarioData(
        id=scenario_id,
        title="Live Analyzed Session",
        description="Dynamic trace analysis",
        user_prompt=user_prompt,
        stats=SessionStats(
            files_inspected=3,
            functions_analyzed=5,
            relevant_paths=2,
            tests_run=2,
            execution_time_seconds=10,
        ),
        events=[],
        logical_changes=[
            LogicalChange(
                id="lc_1",
                title="Commonize retry policy",
                category="COMMONIZATION",
                why_explanation="Centralize retry attempts",
                how_arrived_steps=["Observed duplicated retry logic"],
                epistemic_status="INFERRED",
                confidence_score=0.95,
                hunks=hunks,
                affected_services=["src/services/PaymentService.py"],
                blast_radius="service_level",
                review_checklist=[
                    ReviewChecklistItem(item="Ensure backoff limit", severity="CRITICAL", rationale="Prevent server storm")
                ],
            )
        ],
        attention_items=attention_items,
        flow_diff=ExecutionFlowDiff(
            before_flow=FlowDescription(description="Before", steps=[]),
            after_flow=FlowDescription(description="After", steps=[]),
            mermaid_diagram="graph TD;\nA-->B;",
        ),
        causal_graph={"nodes": [], "edges": []},
        grounded_concept=GroundedConcept(
            name="Exponential Backoff",
            headline="Retry with backoff",
            what_it_is="Repeated retries with growing intervals",
            how_your_repo_uses_it="RetryExecutor helper class",
            code_snippet="class RetryExecutor: pass",
            pitfalls_to_watch=["Server storm"],
            related_concepts=["Circuit Breaker"],
        ),
    )


class TestArchitecturalImpactEngine:

    def test_build_from_scenario_mind_map(self):
        scenario = _make_scenario()
        res = architectural_impact_engine.build_from_scenario(scenario)

        assert isinstance(res, ArchitecturalGraphResponse)
        assert len(res.stages) == 5
        stage_ids = [s.id for s in res.stages]
        assert stage_ids == ["trigger", "abstraction", "service", "consumer", "safety"]

        # Mind map should be compact: 5 to 7 nodes total
        assert 5 <= len(res.nodes) <= 7
        # Edges should be sparse and linear: 4 to 8 edges total
        assert 4 <= len(res.edges) <= 8

        # Check nodes cover all 5 stages
        node_stages = {n.stage for n in res.nodes}
        assert "trigger" in node_stages
        assert "abstraction" in node_stages
        assert "service" in node_stages
        assert "consumer" in node_stages
        assert "safety" in node_stages

        # Verify no file paths or line numbers in any node title
        for n in res.nodes:
            assert "/" not in n.title, f"Raw file path found in title: {n.title}"
            assert not n.title.endswith(".py") and not n.title.endswith(".ts")
            assert "Lines" not in n.title

        # Verify edges connect stages without dangling references
        node_ids = {n.id for n in res.nodes}
        for e in res.edges:
            assert e.source in node_ids, f"Dangling edge source: {e.source}"
            assert e.target in node_ids, f"Dangling edge target: {e.target}"

        # Verify narrative exists and is descriptive
        assert len(res.narrative) > 20
        assert res.blast_level in ("localized", "service_level", "systemic")

    def test_build_from_scenario_unique_node_ids(self):
        scenario = _make_scenario(scenario_id="live-auth-check", user_prompt="Add token refresh mutex")
        res = architectural_impact_engine.build_from_scenario(scenario)

        assert isinstance(res, ArchitecturalGraphResponse)
        assert len(res.nodes) >= 5
        assert len(res.edges) >= 4

        # Check node IDs uniqueness
        node_ids = [n.id for n in res.nodes]
        assert len(node_ids) == len(set(node_ids)), "Node IDs must be unique"

    def test_build_from_live_diff_mind_map(self):
        hunks = [
            _make_hunk(
                hunk_id="h1",
                file_path="src/api/client.py",
                symbol="fetch_data",
                change_type="MODIFIED",
            ),
            _make_hunk(
                hunk_id="h2",
                file_path="src/utils/cache.py",
                symbol="CacheManager",
                change_type="ADDED",
            ),
        ]
        res = architectural_impact_engine.build_from_live_diff(
            hunks=hunks,
            user_prompt="Add in-memory caching",
        )

        assert isinstance(res, ArchitecturalGraphResponse)
        # Exactly 5 stages/nodes for standard live diff
        assert 5 <= len(res.nodes) <= 6
        assert len(res.edges) >= 4

        # Verify high-level conceptual titles
        strategy_nodes = [n for n in res.nodes if n.stage == "abstraction"]
        assert len(strategy_nodes) >= 1
        assert "Caching" in strategy_nodes[0].title or "Persistence" in strategy_nodes[0].title or "Strategy" in strategy_nodes[0].title or "Modular" in strategy_nodes[0].title

    def test_unattended_nodes_produce_alert_edges(self):
        hunks = [
            _make_hunk(
                hunk_id="h1",
                file_path="src/service.py",
                symbol="do_work",
                change_type="MODIFIED",
            )
        ]
        mock_sig = SemanticImpactGraph(
            change_set_id="test_cs",
            repository="test_repo",
            nodes=[
                ImpactNode(id="n1", node_type="symbol", name="do_work", change_status="modified", file="src/service.py"),
                ImpactNode(
                    id="n2",
                    node_type="symbol",
                    name="stale_worker",
                    change_status="unchanged",
                    file="src/worker.py",
                    data={"is_unattended": True, "omission_risk": "critical_breaking"},
                ),
            ],
            edges=[
                ImpactEdge(id="e1", source="n2", target="n1", relationship="CALLS"),
            ],
        )

        res = architectural_impact_engine.build_from_live_diff(
            hunks=hunks,
            impact_graph=mock_sig,
        )

        assert res.unattended_count >= 1
        watchout_nodes = [n for n in res.nodes if n.stage == "consumer"]
        assert len(watchout_nodes) == 1
        assert watchout_nodes[0].status == "unattended"
        assert watchout_nodes[0].badge == "ATTENTION REQUIRED"

        # Check edge with alert style
        alert_edges = [e for e in res.edges if e.type == "omission_risk" or e.style == "alert"]
        assert len(alert_edges) >= 1

    def test_gemini_client_architectural_mind_map_offline_fallback(self):
        client = GeminiClient(api_key="")
        result = client.synthesize_architectural_mind_map(
            prompt="Centralize retry logic with backoff",
            files_modified=["src/PaymentService.ts", "src/RefundService.ts"],
            abstractions=["RetryExecutor"],
            unattended_callers=["SubscriptionService.ts"],
            blast_level="service_level",
        )

        assert isinstance(result, dict)
        assert "catalyst" in result
        assert "strategy" in result
        assert "capability" in result
        assert "watchout" in result
        assert "guarantee" in result
        assert "Exponential Backoff" in result["strategy"]["title"]
        assert result["verdict"] == "NEEDS_REVIEW"
        assert result["source"] == "AgentTrace Grounded Mind Map (Local)"

