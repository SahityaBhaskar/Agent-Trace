"""
Unit tests for SemanticImpactGraph.get_chunk_subgraph().

Covers:
  - Resolution by chunk_id and by chunk_index
  - Node set correctness (chunk, file, symbols, direct/trans/handled/unattended)
  - No orphaned edges (both endpoints must be in the subgraph)
  - Graceful handling of unknown chunk_id / out-of-range index
  - Serialisation of the subgraph via model_dump_json
"""

import pytest
from agent_trace.models.impact_graph import (
    ImpactNode,
    ImpactEdge,
    ChunkImpact,
    SemanticImpactGraph,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _node(nid: str, node_type: str = "symbol", change_status: str | None = None) -> ImpactNode:
    return ImpactNode(
        id=nid,
        node_type=node_type,  # type: ignore[arg-type]
        name=nid.split("::")[-1],
        change_status=change_status,
    )


def _edge(eid: str, src: str, tgt: str, rel: str = "CALLS") -> ImpactEdge:
    return ImpactEdge(id=eid, source=src, target=tgt, relationship=rel)  # type: ignore[arg-type]


def _chunk(idx: int, chunk_id: str, file: str = "foo.py", **kwargs) -> ChunkImpact:
    return ChunkImpact(
        chunk_id=chunk_id,
        file=file,
        changed_lines=[10, 20],
        **kwargs,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def two_chunk_graph() -> SemanticImpactGraph:
    """
    Graph with two chunks:
      chunk_a  →  file_a  →  sym_a_1, sym_a_2
                  sym_a_2 is called by caller_a (direct) and transitive_a (transitive)
                  unattended_a is an unattended caller

      chunk_b  →  file_b  →  sym_b_1
                  sym_b_1 is called by caller_b (direct)
    """
    chunk_a_id = "chunk:abc:foo.py:0"
    chunk_b_id = "chunk:abc:bar.py:1"

    nodes = [
        # Chunk A scope
        _node(chunk_a_id, node_type="change_chunk"),
        _node("file:foo.py", node_type="file"),
        _node("sym_a_1", change_status="modified"),
        _node("sym_a_2", change_status="modified"),
        _node("caller_a"),
        _node("transitive_a"),
        _node("unattended_a", change_status=None),
        # Chunk B scope
        _node(chunk_b_id, node_type="change_chunk"),
        _node("file:bar.py", node_type="file"),
        _node("sym_b_1", change_status="added"),
        _node("caller_b"),
        # An edge between the two scopes (should be excluded from chunk-A subgraph)
        _node("cross_node"),
    ]

    edges = [
        # Chunk A internal structure
        _edge("e1", chunk_a_id, "file:foo.py", "CONTAINS"),
        _edge("e2", chunk_a_id, "sym_a_1",     "CONTAINS"),
        _edge("e3", chunk_a_id, "sym_a_2",     "CONTAINS"),
        _edge("e4", "caller_a",    "sym_a_2",  "CALLS"),
        _edge("e5", "transitive_a","caller_a",  "CALLS"),
        _edge("e6", "unattended_a","sym_a_2",  "REFERENCES"),
        # Chunk B internal structure
        _edge("e7", chunk_b_id, "file:bar.py", "CONTAINS"),
        _edge("e8", chunk_b_id, "sym_b_1",     "CONTAINS"),
        _edge("e9", "caller_b",   "sym_b_1",   "CALLS"),
        # Cross-chunk edge (should be excluded when scoping to chunk A)
        _edge("e10", "cross_node", "sym_a_1", "IMPORTS"),
        # cross_node → sym_b_1 (excluded from chunk-A subgraph entirely)
        _edge("e11", "cross_node", "sym_b_1", "IMPORTS"),
    ]

    chunk_a = _chunk(
        0,
        chunk_a_id,
        file="foo.py",
        changed_symbols=["sym_a_1", "sym_a_2"],
        direct_affected=["caller_a"],
        transitive_affected=["transitive_a"],
        unattended_affected=["unattended_a"],
    )
    chunk_b = _chunk(
        1,
        chunk_b_id,
        file="bar.py",
        changed_symbols=["sym_b_1"],
        direct_affected=["caller_b"],
    )

    return SemanticImpactGraph(
        change_set_id="abc",
        repository="myrepo",
        nodes=nodes,
        edges=edges,
        chunks=[chunk_a, chunk_b],
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestGetChunkSubgraphResolution:
    def test_resolve_by_chunk_id(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_id="chunk:abc:foo.py:0")
        assert sub is not None
        assert len(sub.chunks) == 1
        assert sub.chunks[0].chunk_id == "chunk:abc:foo.py:0"

    def test_resolve_by_chunk_index_zero(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        assert sub is not None
        assert sub.chunks[0].chunk_id == "chunk:abc:foo.py:0"

    def test_resolve_by_chunk_index_one(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=1)
        assert sub is not None
        assert sub.chunks[0].chunk_id == "chunk:abc:bar.py:1"

    def test_unknown_chunk_id_returns_none(self, two_chunk_graph):
        assert two_chunk_graph.get_chunk_subgraph(chunk_id="chunk:abc:nope:99") is None

    def test_out_of_range_index_returns_none(self, two_chunk_graph):
        assert two_chunk_graph.get_chunk_subgraph(chunk_index=999) is None

    def test_negative_index_returns_none(self, two_chunk_graph):
        assert two_chunk_graph.get_chunk_subgraph(chunk_index=-1) is None

    def test_no_selector_returns_none(self, two_chunk_graph):
        # Both optional params omitted → None
        assert two_chunk_graph.get_chunk_subgraph() is None


class TestGetChunkSubgraphNodeSet:
    def test_chunk_node_included(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        node_ids = {n.id for n in sub.nodes}
        assert "chunk:abc:foo.py:0" in node_ids

    def test_file_node_included(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        node_ids = {n.id for n in sub.nodes}
        assert "file:foo.py" in node_ids

    def test_changed_symbols_included(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        node_ids = {n.id for n in sub.nodes}
        assert "sym_a_1" in node_ids
        assert "sym_a_2" in node_ids

    def test_direct_affected_included(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        assert "caller_a" in {n.id for n in sub.nodes}

    def test_transitive_affected_included(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        assert "transitive_a" in {n.id for n in sub.nodes}

    def test_unattended_affected_included(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        assert "unattended_a" in {n.id for n in sub.nodes}

    def test_other_chunk_nodes_excluded(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        node_ids = {n.id for n in sub.nodes}
        assert "chunk:abc:bar.py:1" not in node_ids
        assert "sym_b_1" not in node_ids
        assert "caller_b" not in node_ids
        assert "file:bar.py" not in node_ids


class TestGetChunkSubgraphEdgeIntegrity:
    def test_no_orphaned_edges(self, two_chunk_graph):
        """All edges in subgraph must have both source and target present."""
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        node_ids = {n.id for n in sub.nodes}
        for e in sub.edges:
            assert e.source in node_ids, f"edge {e.id}: source {e.source!r} not in subgraph"
            assert e.target in node_ids, f"edge {e.id}: target {e.target!r} not in subgraph"

    def test_internal_edges_retained(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        edge_ids = {e.id for e in sub.edges}
        # These edges are fully inside chunk-A scope
        assert "e1" in edge_ids  # chunk_a → file:foo.py
        assert "e2" in edge_ids  # chunk_a → sym_a_1
        assert "e4" in edge_ids  # caller_a → sym_a_2

    def test_cross_chunk_edge_excluded(self, two_chunk_graph):
        """e11 (cross_node → sym_b_1) must not appear in chunk-A subgraph."""
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        edge_ids = {e.id for e in sub.edges}
        assert "e11" not in edge_ids

    def test_cross_node_edge_excluded_when_node_not_in_scope(self, two_chunk_graph):
        """e10 (cross_node → sym_a_1): cross_node is not in chunk-A blast radius
        so the edge is pruned even though sym_a_1 is in scope."""
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        edge_ids = {e.id for e in sub.edges}
        assert "e10" not in edge_ids

    def test_chunk_b_subgraph_only_has_b_edges(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=1)
        edge_ids = {e.id for e in sub.edges}
        assert "e7" in edge_ids   # chunk_b → file:bar.py
        assert "e8" in edge_ids   # chunk_b → sym_b_1
        assert "e9" in edge_ids   # caller_b → sym_b_1
        # Chunk-A edges must not appear
        assert "e1" not in edge_ids
        assert "e4" not in edge_ids


class TestGetChunkSubgraphMetadata:
    def test_change_set_id_preserved(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        assert sub.change_set_id == "abc"

    def test_repository_preserved(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        assert sub.repository == "myrepo"

    def test_chunks_list_has_one_entry(self, two_chunk_graph):
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        assert len(sub.chunks) == 1

    def test_serialization_roundtrip(self, two_chunk_graph):
        """Subgraph must be JSON-serializable and round-trip via model_dump_json."""
        import json
        sub = two_chunk_graph.get_chunk_subgraph(chunk_index=0)
        raw = sub.model_dump_json()
        data = json.loads(raw)
        assert "nodes" in data
        assert "edges" in data
        assert "chunks" in data
        assert len(data["chunks"]) == 1
        # All node IDs round-trip
        orig_ids = {n.id for n in sub.nodes}
        rt_ids = {n["id"] for n in data["nodes"]}
        assert orig_ids == rt_ids


class TestGetChunkSubgraphEdgeCases:
    def test_empty_blast_radius_chunk(self):
        """A chunk with no affected symbols returns just the chunk + file nodes."""
        chunk_id = "chunk:x:empty.py:0"
        nodes = [
            _node(chunk_id, node_type="change_chunk"),
            _node("file:empty.py", node_type="file"),
        ]
        edges = [
            _edge("e1", chunk_id, "file:empty.py", "CONTAINS"),
        ]
        chunk = ChunkImpact(chunk_id=chunk_id, file="empty.py", changed_lines=[1, 5])
        graph = SemanticImpactGraph(
            change_set_id="x", repository="r", nodes=nodes, edges=edges, chunks=[chunk]
        )
        sub = graph.get_chunk_subgraph(chunk_index=0)
        assert sub is not None
        node_ids = {n.id for n in sub.nodes}
        assert chunk_id in node_ids
        assert "file:empty.py" in node_ids

    def test_graph_with_no_chunks_returns_none(self):
        graph = SemanticImpactGraph(change_set_id="x", repository="r")
        assert graph.get_chunk_subgraph(chunk_index=0) is None
        assert graph.get_chunk_subgraph(chunk_id="anything") is None

    def test_chunk_id_takes_priority_over_index(self, two_chunk_graph):
        """chunk_id is resolved first; chunk_index is ignored when chunk_id is given."""
        # Provide index 1 (chunk_b) but id matching chunk_a
        sub = two_chunk_graph.get_chunk_subgraph(
            chunk_id="chunk:abc:foo.py:0",
            chunk_index=1,
        )
        assert sub is not None
        assert sub.chunks[0].chunk_id == "chunk:abc:foo.py:0"
