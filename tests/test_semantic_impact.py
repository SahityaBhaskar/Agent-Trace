"""
Unit tests for the Semantic Impact Graph pipeline.

Covers:
  - ImpactNode / ImpactEdge / SemanticImpactGraph models
  - SerenaClient output parsers (no subprocess required)
  - DiffSymbolMapper  (AST + heuristic paths, no Serena)
  - SemanticImpactGraphBuilder (full build with mock hunks, no Serena)
"""

import pytest
from agent_trace.models.impact_graph import (
    ImpactNode, ImpactEdge, SemanticImpactGraph, ChunkImpact
)
from agent_trace.engine.serena_client import SerenaClient
from agent_trace.engine.diff_symbol_mapper import (
    DiffSymbolMapper, _make_symbol_id, _make_file_id, _make_chunk_id,
)
from agent_trace.engine.semantic_impact import SemanticImpactGraphBuilder
from agent_trace.models.report import DiffHunk
from agent_trace.models.jev_types import (
    JevEvaluationResult, JevClassificationResult, JevBlastRadiusResult,
    JevHumanReviewResult, JevBreakingRiskResult,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_jev_result() -> JevEvaluationResult:
    return JevEvaluationResult(
        hunk_id="test_hunk",
        source="calibrated_cache",
        classification=JevClassificationResult(category="REFACTOR", confidence=0.80, choice="REFACTOR"),
        blast_radius=JevBlastRadiusResult(level="localized", score=1.5),
        human_review=JevHumanReviewResult(required=False, probability=0.1),
        breaking_risk=JevBreakingRiskResult(is_breaking=False, probability=0.1),
        latency_ms=0,
    )


def _make_hunk(
    hunk_id="h1",
    file_path="src/foo.py",
    symbol="my_function",
    change_type="MODIFIED",
    line_range="Lines 10–25",
    old_lines="def my_function():\n    pass",
    new_lines="def my_function():\n    return 42",
) -> DiffHunk:
    return DiffHunk(
        id=hunk_id,
        file_path=file_path,
        symbol=symbol,
        old_lines=old_lines,
        new_lines=new_lines,
        line_range=line_range,
        change_type=change_type,
        jev_result=_make_jev_result(),
        evidence_event_ids=[],
    )


# ── Model tests ────────────────────────────────────────────────────────────────

class TestImpactGraphModels:

    def test_impact_node_defaults(self):
        n = ImpactNode(id="test::fn", node_type="symbol", name="fn")
        assert n.id == "test::fn"
        assert n.node_type == "symbol"
        assert n.change_status is None
        assert n.data == {}

    def test_impact_edge_defaults(self):
        e = ImpactEdge(id="e1", source="a", target="b", relationship="CALLS")
        assert e.confidence == 1.0
        assert e.distance == "DIRECT"
        assert e.evidence_source == "unresolved"

    def test_semantic_impact_graph_query_helpers(self):
        n1 = ImpactNode(id="n1", node_type="symbol", name="foo", change_status="modified")
        n2 = ImpactNode(id="n2", node_type="symbol", name="bar", change_status="unchanged")
        e  = ImpactEdge(id="e1", source="n2", target="n1", relationship="CALLS")
        g  = SemanticImpactGraph(change_set_id="abc", repository="repo", nodes=[n1, n2], edges=[e])

        assert g.get_node("n1") is n1
        assert g.get_node("missing") is None
        assert len(g.get_edges_to("n1")) == 1
        assert len(g.get_edges_from("n2")) == 1
        consumers = g.get_consumers("n1")
        assert any(c.id == "n2" for c in consumers)

    def test_changed_nodes_filter(self):
        nodes = [
            ImpactNode(id="a", node_type="symbol", name="a", change_status="added"),
            ImpactNode(id="b", node_type="symbol", name="b", change_status="unchanged"),
            ImpactNode(id="c", node_type="symbol", name="c", change_status="deleted"),
        ]
        g = SemanticImpactGraph(change_set_id="x", repository="r", nodes=nodes)
        changed = g.changed_nodes()
        assert {n.id for n in changed} == {"a", "c"}

    def test_to_cytoscape_shape(self):
        n = ImpactNode(id="n1", node_type="symbol", name="foo", change_status="modified")
        e = ImpactEdge(id="e1", source="n1", target="n2", relationship="CALLS", confidence=0.95)
        g = SemanticImpactGraph(change_set_id="x", repository="r", nodes=[n], edges=[e])
        cy = g.to_cytoscape()
        assert "elements" in cy
        assert len(cy["elements"]["nodes"]) == 1
        assert cy["elements"]["nodes"][0]["data"]["changeStatus"] == "modified"
        assert cy["elements"]["edges"][0]["data"]["relationship"] == "CALLS"


# ── SerenaClient parser tests ──────────────────────────────────────────────────

class TestSerenaClientParsers:

    def test_parse_symbols_overview_basic(self):
        raw = """
## src/foo.py
- `MyClass` (class) [line 5-50]
  - `my_method` (method) [line 10-20]
- `standalone_fn` (function) [line 55-70]
"""
        syms = SerenaClient.parse_symbols_overview(raw)
        names = [s["name"] for s in syms]
        assert "MyClass" in names
        assert "my_method" in names
        assert "standalone_fn" in names

    def test_parse_symbols_overview_line_numbers(self):
        raw = "## myfile.py\n- `process_data` (function) [line 10-30]\n"
        syms = SerenaClient.parse_symbols_overview(raw)
        assert len(syms) == 1
        assert syms[0]["start_line"] == 10
        assert syms[0]["end_line"] == 30

    def test_parse_symbols_overview_empty(self):
        assert SerenaClient.parse_symbols_overview("") == []
        assert SerenaClient.parse_symbols_overview(None) == []

    def test_parse_referencing_symbols_basic(self):
        raw = """
## src/client.py
- `configureInterceptors` (function) [line 42]
- `createApiClient` (function) [line 60]
"""
        refs = SerenaClient.parse_referencing_symbols(raw)
        assert len(refs) == 2
        assert refs[0]["name"] == "configureInterceptors"
        assert refs[0]["file"] == "src/client.py"
        assert refs[0]["line"] == 42

    def test_parse_referencing_symbols_empty(self):
        assert SerenaClient.parse_referencing_symbols("") == []
        assert SerenaClient.parse_referencing_symbols(None) == []


# ── DiffSymbolMapper tests ─────────────────────────────────────────────────────

class TestDiffSymbolMapper:

    def setup_method(self):
        # No Serena — all tests exercise AST/heuristic paths
        self.mapper = DiffSymbolMapper(serena=None)

    def test_map_python_function(self, tmp_path):
        code = "def my_function():\n    pass\n\ndef other():\n    return 1\n"
        f = tmp_path / "test.py"
        f.write_text(code)
        nodes = self.mapper.map_chunk_to_symbols(
            file_path="test.py",
            start_line=1, end_line=2,
            change_status="modified",
            change_set_id="cs1", chunk_index=0,
            repo_root=str(tmp_path),
        )
        assert len(nodes) >= 1
        names = [n.name for n in nodes]
        assert any("my_function" in nm for nm in names)

    def test_map_python_class(self, tmp_path):
        code = "class MyClass:\n    def method(self):\n        pass\n"
        f = tmp_path / "cls.py"
        f.write_text(code)
        nodes = self.mapper.map_chunk_to_symbols(
            file_path="cls.py",
            start_line=1, end_line=3,
            change_status="added",
            change_set_id="cs1", chunk_index=0,
            repo_root=str(tmp_path),
        )
        assert len(nodes) >= 1

    def test_unresolved_when_no_match(self, tmp_path):
        code = "x = 1\ny = 2\n"
        f = tmp_path / "plain.py"
        f.write_text(code)
        nodes = self.mapper.map_chunk_to_symbols(
            file_path="plain.py",
            start_line=1, end_line=2,
            change_status="modified",
            change_set_id="cs1", chunk_index=0,
            repo_root=str(tmp_path),
        )
        # Should return either a heuristic match or UNRESOLVED — never empty
        assert len(nodes) >= 1

    def test_make_file_node(self):
        node = self.mapper.make_file_node("src/foo.py", "modified")
        assert node.node_type == "file"
        assert node.id == _make_file_id("src/foo.py")
        assert node.change_status == "modified"

    def test_make_chunk_node(self):
        node = self.mapper.make_chunk_node(
            file_path="src/bar.py",
            start_line=10, end_line=30,
            change_set_id="cs1", chunk_index=2,
            change_status="added",
        )
        assert node.node_type == "change_chunk"
        assert node.id == _make_chunk_id("cs1", "src/bar.py", 2)

    def test_canonical_symbol_id(self):
        sid = _make_symbol_id("src/foo.py", "MyClass")
        assert "src/foo.py" in sid
        assert "MyClass" in sid


# ── SemanticImpactGraphBuilder tests ──────────────────────────────────────────

class TestSemanticImpactGraphBuilder:

    def setup_method(self):
        self.builder = SemanticImpactGraphBuilder(
            serena=None,  # no Serena in unit tests
            repo_path=".",
            max_depth=1,
        )

    def test_build_empty_hunks(self):
        graph = self.builder.build(hunks=[], change_set_id="cs1", repository="repo")
        assert isinstance(graph, SemanticImpactGraph)
        assert graph.change_set_id == "cs1"
        assert graph.nodes == []
        assert graph.edges == []

    def test_build_single_hunk(self):
        hunk = _make_hunk(hunk_id="h1", file_path="src/foo.py", line_range="Lines 10–25")
        graph = self.builder.build(hunks=[hunk], change_set_id="cs1", repository="repo")
        assert isinstance(graph, SemanticImpactGraph)
        # Should have at minimum a chunk node and a file node
        node_types = {n.node_type for n in graph.nodes}
        assert "change_chunk" in node_types
        assert "file" in node_types

    def test_build_multiple_hunks_deduplication(self):
        hunk1 = _make_hunk(hunk_id="h1", file_path="src/foo.py", line_range="Lines 1–10")
        hunk2 = _make_hunk(hunk_id="h2", file_path="src/foo.py", line_range="Lines 20–30")
        graph = self.builder.build(hunks=[hunk1, hunk2], change_set_id="cs1", repository="repo")
        # File node should NOT be duplicated
        file_nodes = [n for n in graph.nodes if n.node_type == "file" and n.file == "src/foo.py"]
        assert len(file_nodes) == 1

    def test_no_self_edges(self):
        hunk = _make_hunk(hunk_id="h1", file_path="src/bar.py", line_range="Lines 5–15")
        graph = self.builder.build(hunks=[hunk], change_set_id="cs1", repository="repo")
        for e in graph.edges:
            assert e.source != e.target, f"Self-edge found: {e.source}"

    def test_chunk_impact_populated(self):
        hunk = _make_hunk(hunk_id="h1", file_path="src/baz.py", line_range="Lines 1–5")
        graph = self.builder.build(hunks=[hunk], change_set_id="cs1", repository="repo")
        assert len(graph.chunks) == 1
        ci = graph.chunks[0]
        assert isinstance(ci, ChunkImpact)
        assert ci.file == "src/baz.py"

    def test_parse_line_range(self):
        parse = SemanticImpactGraphBuilder._parse_line_range
        assert parse("Lines 10–45") == (10, 45)
        assert parse("Lines 5–5") == (5, 5)
        assert parse("L10-20") == (10, 20)
        assert parse("unknown") == (1, 1)

    def test_build_added_hunk_change_status(self):
        hunk = _make_hunk(hunk_id="h1", file_path="src/new.py", change_type="ADDED", line_range="Lines 1–10")
        graph = self.builder.build(hunks=[hunk], change_set_id="cs1", repository="repo")
        chunk_nodes = [n for n in graph.nodes if n.node_type == "change_chunk"]
        assert len(chunk_nodes) == 1
        assert chunk_nodes[0].change_status == "added"

    def test_max_depth_zero(self):
        """Depth 0 should still produce nodes but no traversal edges."""
        builder0 = SemanticImpactGraphBuilder(serena=None, repo_path=".", max_depth=0)
        hunk = _make_hunk(hunk_id="h1", file_path="src/d0.py", line_range="Lines 1–5")
        graph = builder0.build(hunks=[hunk], change_set_id="cs1", repository="repo")
        assert len(graph.nodes) >= 1

    def test_handled_and_unattended_symbol_partitioning(self):
        """Mock Serena referencing symbols: verify modified caller is handled, untouched caller is unattended."""
        from unittest.mock import MagicMock

        mock_serena = MagicMock()
        mock_serena.activate_project.return_value = True
        # find_referencing_symbols returns two callers for my_function:
        # 1. caller_updated in src/foo.py (which is also in the diff)
        # 2. caller_untouched in src/bar.py (not in diff)
        mock_serena.find_referencing_symbols.return_value = (
            "## src/foo.py\n"
            "- caller_updated (function) [line 30]\n"
            "## src/bar.py\n"
            "- caller_untouched (function) [line 50]\n"
        )
        mock_serena.get_symbols_overview.return_value = ""

        builder = SemanticImpactGraphBuilder(serena=mock_serena, repo_path=".", max_depth=1)

        # hunk1 modifies my_function, hunk2 modifies caller_updated
        hunk1 = _make_hunk(hunk_id="h1", file_path="src/foo.py", symbol="my_function", line_range="Lines 10–20")
        hunk2 = _make_hunk(hunk_id="h2", file_path="src/foo.py", symbol="caller_updated", line_range="Lines 30–35")

        graph = builder.build(hunks=[hunk1, hunk2], change_set_id="cs1", repository="repo")

        # Verify chunk 0 impacts
        ci = graph.chunks[0]
        assert any("caller_updated" in h for h in ci.handled_affected)
        assert any("caller_untouched" in u for u in ci.unattended_affected)

        # Verify get_unattended_nodes helper
        unattended_nodes = graph.get_unattended_nodes()
        assert any(n.name == "caller_untouched" for n in unattended_nodes)
        assert not any(n.name == "caller_updated" for n in unattended_nodes)

        # Verify Cytoscape serialization flags
        cy = graph.to_cytoscape()
        cy_nodes = cy["elements"]["nodes"]
        untouched_cy = next(n for n in cy_nodes if n["data"]["label"] == "caller_untouched")
        assert untouched_cy["data"]["isUnattended"] is True
        assert untouched_cy["data"]["omissionRisk"] in ("high", "medium_test")

