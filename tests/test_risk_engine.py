"""Unit tests for RiskEngine."""
import unittest
from agent_trace.engine.risk_engine import RiskEngine
from agent_trace.models.report import DiffHunk, LogicalChange, ReviewChecklistItem
from agent_trace.models.jev_types import (
    JevEvaluationResult,
    JevClassificationResult,
    JevBlastRadiusResult,
    JevHumanReviewResult,
    JevBreakingRiskResult,
)


def _make_hunk(
    category: str,
    blast_score: float,
    breaking: bool = False,
    human_required: bool = False,
    human_prob: float = 0.5,
) -> DiffHunk:
    jev = JevEvaluationResult(
        hunk_id="h1",
        classification=JevClassificationResult(
            category=category, confidence=0.9, choice=category
        ),
        blast_radius=JevBlastRadiusResult(
            score=blast_score,
            level="system_critical" if blast_score >= 4.0 else "localized",
        ),
        human_review=JevHumanReviewResult(required=human_required, probability=human_prob),
        breaking_risk=JevBreakingRiskResult(is_breaking=breaking, probability=0.9 if breaking else 0.1),
        latency_ms=10,
        source="calibrated_cache",
    )
    return DiffHunk(
        id="hunk1",
        file_path="src/api.py",
        symbol="my_func",
        old_lines="-old",
        new_lines="+new",
        line_range="1-5",
        change_type="MODIFIED",
        jev_result=jev,
    )


def _make_change(hunks, affected_services=None) -> LogicalChange:
    return LogicalChange(
        id="lc1",
        title="Test Change",
        category="REFACTOR",
        why_explanation="Test",
        how_arrived_steps=["step1"],
        epistemic_status="OBSERVED",
        confidence_score=0.8,
        hunks=hunks,
        affected_services=affected_services or [],
        blast_radius="localized",
        review_checklist=[],
    )


class TestRiskEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RiskEngine()

    def test_high_blast_radius_triggers_high(self):
        hunk = _make_hunk("REFACTOR", blast_score=4.0)
        change = _make_change([hunk])
        items = self.engine.evaluate_changes([change])
        levels = [i.level for i in items]
        self.assertIn("HIGH", levels)

    def test_breaking_change_triggers_high(self):
        hunk = _make_hunk("REFACTOR", blast_score=1.0, breaking=True)
        change = _make_change([hunk])
        items = self.engine.evaluate_changes([change])
        high_titles = [i.title for i in items if i.level == "HIGH"]
        self.assertTrue(any("Breaking" in t for t in high_titles))

    def test_security_category_triggers_high(self):
        hunk = _make_hunk("SECURITY_RELEVANT_CHANGE", blast_score=1.0)
        change = _make_change([hunk])
        items = self.engine.evaluate_changes([change])
        high_titles = [i.title for i in items if i.level == "HIGH"]
        self.assertTrue(any("Security" in t for t in high_titles))

    def test_api_change_triggers_medium(self):
        hunk = _make_hunk("API_CHANGE", blast_score=1.0)
        change = _make_change([hunk])
        items = self.engine.evaluate_changes([change])
        medium_titles = [i.title for i in items if i.level == "MEDIUM"]
        self.assertTrue(any("API" in t for t in medium_titles))

    def test_cross_service_coupling(self):
        hunk = _make_hunk("REFACTOR", blast_score=1.0)
        change = _make_change([hunk], affected_services=["svc_a", "svc_b", "svc_c"])
        items = self.engine.evaluate_changes([change])
        medium_titles = [i.title for i in items if i.level == "MEDIUM"]
        self.assertTrue(any("Coupling" in t for t in medium_titles))

    def test_no_false_triggers_on_clean_hunk(self):
        hunk = _make_hunk("REFACTOR", blast_score=1.0)
        change = _make_change([hunk])
        items = self.engine.evaluate_changes([change])
        for item in items:
            self.assertIn(item.level, ("LOW", "MEDIUM"))
        high_items = [i for i in items if i.level == "HIGH"]
        self.assertEqual(high_items, [])

    def test_module_level_symbol_formatting(self):
        """Verify module symbol formatting never produces 'module in ' or broken trailing text."""
        hunk = _make_hunk("REFACTOR", blast_score=1.0, human_required=True, human_prob=0.92)
        hunk.symbol = "module"
        hunk.file_path = "src/pipeline.py"
        change = _make_change([hunk])
        items = self.engine.evaluate_changes([change])
        senior_items = [i for i in items if "Senior Review Required" in i.title]
        self.assertEqual(len(senior_items), 1)
        self.assertIn("Module (src/pipeline.py)", senior_items[0].title)
        self.assertIn("module-level code in src/pipeline.py", senior_items[0].detail)
        self.assertNotIn("for module in ", senior_items[0].detail)

    def test_dynamic_jev_human_review_probabilities(self):
        """Verify dynamic feature scoring produces proportional probabilities across change types."""
        from agent_trace.engine.jev_client import JevClient

        client = JevClient(api_key="")

        # 1. Pure comment change -> minimal review prob
        res_comment = client.evaluate_hunk("h_comm", "src/math.py", "+ # helper docstring", "add comment")
        self.assertFalse(res_comment.human_review.required)
        self.assertLessEqual(res_comment.human_review.probability, 0.20)

        # 2. Localized internal logic -> low review prob
        res_local = client.evaluate_hunk("h_local", "src/math.py", "- x = 1\n+ x = 2", "tweak constant")
        self.assertFalse(res_local.human_review.required)
        self.assertLessEqual(res_local.human_review.probability, 0.40)

        # 3. Security sensitive file -> high review prob
        res_sec = client.evaluate_hunk("h_sec", "src/auth/token_verifier.py", "+ def verify_jwt(): pass", "auth addition")
        self.assertTrue(res_sec.human_review.required)
        self.assertGreaterEqual(res_sec.human_review.probability, 0.80)

        # 4. Critical omission with unattended callers -> high review prob
        res_omission = client.evaluate_hunk(
            "h_om", "src/api.py", "- def call():\n+ def call(v):", "sig change",
            unattended_callers=["src/app.py::main", "src/worker.py::task"],
        )
        self.assertTrue(res_omission.human_review.required)
        self.assertGreaterEqual(res_omission.human_review.probability, 0.90)

    def test_deep_llm_risk_analysis_populated(self):
        """Verify AttentionItem is enriched with diff snippet, graph context, and structured LLM analysis."""
        hunk = _make_hunk("SECURITY_RELEVANT_CHANGE", blast_score=4.0, human_required=True, human_prob=0.95)
        hunk.old_lines = "def get_auth():\n    return token"
        hunk.new_lines = "def get_auth(retry=True):\n    return refresh_token()"
        hunk.symbol = "get_auth"
        hunk.file_path = "src/auth/client.py"

        change = _make_change([hunk], affected_services=["AuthService", "PaymentService"])
        items = self.engine.evaluate_changes([change])

        self.assertTrue(len(items) > 0)
        item = items[0]

        # Field preservation
        self.assertEqual(item.symbol, "get_auth")
        self.assertEqual(item.file_path, "src/auth/client.py")
        self.assertEqual(item.hunk_id, hunk.id)
        self.assertIn("def get_auth", item.diff_snippet)
        self.assertIsNotNone(item.graph_context)
        self.assertEqual(item.graph_context["coupled_services"], ["AuthService", "PaymentService"])

        # Structured LLM analysis
        analysis = item.llm_analysis
        self.assertIsInstance(analysis, dict)
        self.assertIn("summary", analysis)
        self.assertIn("why_at_risk", analysis)
        self.assertIn("jev_signals_breakdown", analysis)
        self.assertIn("code_change_breakdown", analysis)
        self.assertIn("change_graph_breakdown", analysis)
        self.assertIn("recommended_verification", analysis)
        self.assertIn("source", analysis)

        # Grounding checks
        self.assertIn("get_auth", analysis["why_at_risk"])
        self.assertIn("client.py", analysis["why_at_risk"])

    def test_deep_llm_risk_analysis_with_impact_graph(self):
        """Verify semantic impact graph callers are wired into graph_context and reflected in why_at_risk."""
        from unittest.mock import MagicMock
        from agent_trace.models.impact_graph import ChunkImpact

        hunk = _make_hunk("REFACTOR", blast_score=1.5, human_required=True, human_prob=0.88)
        hunk.file_path = "src/payment.py"
        hunk.symbol = "process_payment"
        hunk.old_lines = "- def process_payment(amount)"
        hunk.new_lines = "+ def process_payment(amount, currency='USD')"

        mock_sig = MagicMock()
        mock_chunk = ChunkImpact(
            chunk_id=hunk.id,
            file="src/payment.py",
            handled_affected=["src/checkout.py::pay"],
            unattended_affected=["src/recurring.py::renew", "src/billing.py::charge"],
        )
        mock_sig.chunks = [mock_chunk]

        change = _make_change([hunk], affected_services=["BillingService"])
        items = self.engine.evaluate_changes([change], semantic_impact_graph=mock_sig)

        senior_items = [i for i in items if "Senior Review Required" in i.title]
        self.assertEqual(len(senior_items), 1)
        item = senior_items[0]

        # Verify graph context contains callers from impact graph
        self.assertIn("src/checkout.py::pay", item.graph_context["affected_callers"])
        self.assertIn("src/recurring.py::renew", item.graph_context["unattended_callers"])

        # Verify analysis reflects the unattended callers
        analysis = item.llm_analysis
        self.assertTrue(len(analysis["why_at_risk"]) > 20)
        self.assertIn("unattended caller", analysis["why_at_risk"])

    def test_stale_code_removal_on_commonization(self):
        """Verify COMMONIZATION category triggers a stale code removal suggestion."""
        hunk = _make_hunk("COMMONIZATION", blast_score=1.0)
        hunk.symbol = "extract_retry"
        hunk.file_path = "src/utils/retry.py"
        change = _make_change([hunk])
        items = self.engine.evaluate_changes([change])

        stale_items = [i for i in items if "Stale Code Removal" in i.title]
        self.assertEqual(len(stale_items), 1)
        item = stale_items[0]
        self.assertEqual(item.level, "LOW")
        self.assertIn("Audit and remove stale", item.action_required)
        self.assertIn("extract_retry", item.title)

    def test_stale_code_removal_on_deprecated_hunk(self):
        """Verify deprecated or dead code in hunk diff triggers a stale code removal suggestion."""
        hunk = _make_hunk("REFACTOR", blast_score=1.0)
        hunk.symbol = "legacy_handler"
        hunk.old_lines = "- @deprecated\n- def legacy_handler(): pass"
        hunk.file_path = "src/handlers.py"
        change = _make_change([hunk])
        items = self.engine.evaluate_changes([change])

        stale_items = [i for i in items if "Stale Code Removal" in i.title]
        self.assertEqual(len(stale_items), 1)
        self.assertEqual(stale_items[0].level, "LOW")

    def test_stale_code_removal_keyword_fallback(self):
        """Verify keyword fallback triggers stale code removal when no Jev items are emitted."""
        hunk = _make_hunk("REFACTOR", blast_score=1.0)
        change = _make_change([hunk])
        change.title = "Stale code removal across legacy modules"
        items = self.engine.evaluate_changes([change])

        stale_items = [i for i in items if "Stale Code Removal" in i.title]
        self.assertEqual(len(stale_items), 1)
        self.assertEqual(stale_items[0].level, "LOW")
        self.assertIn("Audit repository for lingering callers", stale_items[0].action_required)


if __name__ == "__main__":
    unittest.main()
