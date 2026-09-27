"""
End-to-end test suite for Serena MCP symbol discovery, unattended caller partitioning,
Jev System 1 omission evaluation, and RiskEngine alerting.
"""

from unittest.mock import MagicMock
import pytest

from agent_trace.engine.jev_client import JevClient
from agent_trace.engine.semantic_impact import SemanticImpactGraphBuilder
from agent_trace.engine.risk_engine import RiskEngine
from agent_trace.models.report import DiffHunk, LogicalChange
from agent_trace.models.jev_types import JevEvaluationResult, JevOmissionResult


class TestOmissionDetectionPipeline:

    def test_jev_client_calibrated_omission_evaluation(self):
        """Test JevClient evaluates unattended callers and omission severity deterministically."""
        client = JevClient(api_key="")  # deterministic fallback

        # Case 1: Multiple non-test unattended callers -> critical_breaking / high omission risk
        res = client.evaluate_hunk(
            hunk_id="h1",
            file_path="src/payment.py",
            diff_text="- def charge():\n+ def charge(amount, currency):",
            agent_context="Changed signature",
            handled_callers=["src/checkout.py::CheckoutService"],
            unattended_callers=["src/recurring.py::RecurringJob", "src/refunds.py::RefundService"],
        )
        assert isinstance(res, JevEvaluationResult)
        assert res.omission is not None
        assert res.omission.has_omission_risk is True
        assert res.omission.severity == "critical_breaking"
        assert res.omission.probability >= 0.8
        assert "RecurringJob" in res.omission.rationale
        assert res.breaking_risk.is_breaking is True
        assert res.human_review.required is True

        # Case 2: Only test caller unattended -> low severity omission
        res_test = client.evaluate_hunk(
            hunk_id="h2",
            file_path="src/payment.py",
            diff_text="- return 1\n+ return 2",
            agent_context="Logic update",
            handled_callers=["src/checkout.py::CheckoutService"],
            unattended_callers=["tests/test_payment.py::test_charge"],
        )
        assert res_test.omission is not None
        assert res_test.omission.has_omission_risk is True
        assert res_test.omission.severity == "low"
        assert res_test.breaking_risk.is_breaking is False

        # Case 3: Zero unattended callers -> no omission risk
        res_clean = client.evaluate_hunk(
            hunk_id="h3",
            file_path="src/payment.py",
            diff_text="+ # comment",
            agent_context="Comment added",
            handled_callers=["src/checkout.py::CheckoutService"],
            unattended_callers=[],
        )
        assert res_clean.omission is not None
        assert res_clean.omission.has_omission_risk is False
        assert res_clean.omission.severity == "none"

    def test_risk_engine_rule8_omission_alert(self):
        """Test RiskEngine Rule 8 triggers high-priority attention item when omission risk is present."""
        client = JevClient(api_key="")
        jev_res = client.evaluate_hunk(
            hunk_id="h1",
            file_path="src/auth.py",
            diff_text="- def verify():\n+ def verify(token, strict=True):",
            agent_context="Auth update",
            handled_callers=[],
            unattended_callers=["src/gateway.py::ApiGateway", "src/admin.py::AdminPortal"],
        )

        hunk = DiffHunk(
            id="h1",
            file_path="src/auth.py",
            symbol="verify",
            old_lines="def verify(): pass",
            new_lines="def verify(token, strict=True): pass",
            line_range="Lines 10–20",
            change_type="MODIFIED",
            jev_result=jev_res,
            evidence_event_ids=[],
        )

        change = LogicalChange(
            id="change_1",
            title="Auth verification change",
            category="API_CHANGE",
            why_explanation="Updated auth verification",
            how_arrived_steps=[],
            epistemic_status="OBSERVED",
            confidence_score=0.95,
            hunks=[hunk],
            affected_services=["src/auth.py"],
            blast_radius="service_level",
            review_checklist=[],
        )

        risk_eng = RiskEngine()
        items = risk_eng.evaluate_changes([change])

        # Verify Rule 8 emitted an AttentionItem for verify
        omission_items = [it for it in items if "Unattended Caller Risk" in it.title]
        assert len(omission_items) == 1
        it = omission_items[0]
        assert it.level == "HIGH"
        assert "ApiGateway" in it.detail or "AdminPortal" in it.detail
        assert "Audit untouched callers" in it.action_required

    def test_full_pipeline_serena_to_jev_to_risk(self):
        """Full end-to-end integration: mock Serena -> builder -> Jev -> RiskEngine."""
        mock_serena = MagicMock()
        mock_serena.activate_project.return_value = True
        mock_serena.find_referencing_symbols.return_value = (
            "## src/client.py\n"
            "- client_caller (function) [line 12]\n"
            "## src/orphan.py\n"
            "- orphan_consumer (function) [line 88]\n"
        )
        mock_serena.get_symbols_overview.return_value = ""

        builder = SemanticImpactGraphBuilder(serena=mock_serena, repo_path=".", max_depth=1)

        # In this diff:
        # hunk1 modifies target_func
        # hunk2 modifies client_caller
        # orphan_consumer is NOT in the diff (unattended!)
        hunk1 = DiffHunk(
            id="h1",
            file_path="src/target.py",
            symbol="target_func",
            old_lines="def target_func(): pass",
            new_lines="def target_func(): return True",
            line_range="Lines 1–10",
            change_type="MODIFIED",
            jev_result=JevClient().evaluate_hunk("h1", "src/target.py", "", "init"),
            evidence_event_ids=[],
        )
        hunk2 = DiffHunk(
            id="h2",
            file_path="src/client.py",
            symbol="client_caller",
            old_lines="def client_caller(): pass",
            new_lines="def client_caller(): return target_func()",
            line_range="Lines 10–20",
            change_type="MODIFIED",
            jev_result=JevClient().evaluate_hunk("h2", "src/client.py", "", "init"),
            evidence_event_ids=[],
        )

        graph = builder.build(hunks=[hunk1, hunk2], change_set_id="commit_1", repository="test_repo")

        # Verify impact graph chunk 0 partition
        chunk0 = graph.chunks[0]
        assert any("client_caller" in h for h in chunk0.handled_affected)
        assert any("orphan_consumer" in u for u in chunk0.unattended_affected)

        # Feed back to Jev System 1
        jev = JevClient()
        hunk1.jev_result = jev.evaluate_hunk(
            hunk_id=hunk1.id,
            file_path=hunk1.file_path,
            diff_text="+ return True",
            agent_context="Modified target_func",
            handled_callers=chunk0.handled_affected,
            unattended_callers=chunk0.unattended_affected,
        )

        assert hunk1.jev_result.omission is not None
        assert hunk1.jev_result.omission.has_omission_risk is True
        assert any("orphan_consumer" in u for u in hunk1.jev_result.omission.unattended_symbols)

        # Risk engine
        change = LogicalChange(
            id="c1",
            title="Modified target",
            category="REFACTOR",
            why_explanation="",
            how_arrived_steps=[],
            epistemic_status="OBSERVED",
            confidence_score=0.9,
            hunks=[hunk1, hunk2],
            affected_services=["src/target.py", "src/client.py"],
            blast_radius="localized",
            review_checklist=[],
        )
        items = RiskEngine().evaluate_changes([change])
        assert any("Unattended Caller Risk: target_func" in it.title for it in items)

    def test_transcript_watcher_generate_scenario_from_repo(self):
        """Test transcript_watcher.generate_scenario_from_repo executes without NameError or missing jev_client."""
        from agent_trace.engine.transcript_watcher import transcript_watcher
        import tempfile
        import subprocess
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_path = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)

            file1 = repo_path / "calc.py"
            file1.write_text("def add(a, b):\n    return a + b\n")
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_path, check=True)

            # Modify calc.py
            file1.write_text("def add(a, b, c=0):\n    return a + b + c\n")

            scenario = transcript_watcher.generate_scenario_from_repo(
                repo_path=str(repo_path),
                diff_target="auto",
                user_prompt="Add parameter c to add",
            )
            assert scenario is not None
            assert scenario.id == "live-repository"
            assert len(scenario.logical_changes) > 0
            assert scenario.causal_graph is not None
            assert scenario.semantic_impact_graph is not None
