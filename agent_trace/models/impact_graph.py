"""
Semantic Impact Graph models.

Separate from the Causal Graph (models/graph.py) which represents the
agent-action timeline.  This graph represents *code-level* relationships:
which symbols reference / call / import / implement other symbols, and
which symbols are in the blast radius of a diff chunk.

Graph levels
------------
Level 1 – Symbol graph  : fine-grained symbol → symbol edges
Level 2 – Chunk graph   : diff chunk → affected files/symbols

Every node and edge carries:
  - evidence_source  : how the relationship was discovered
  - confidence       : float 0–1
  - epistemic_status : OBSERVED | INFERRED | TEXTUAL_ONLY | UNRESOLVED
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

# ── Relationship types ────────────────────────────────────────────────────────

RelationshipType = Literal[
    "IMPORTS",
    "EXPORTS",
    "CALLS",
    "REFERENCES",
    "READS",
    "WRITES",
    "TYPE_REFERENCE",
    "PARAMETER_TYPE",
    "RETURN_TYPE",
    "EXTENDS",
    "IMPLEMENTS",
    "INSTANTIATES",
    "OVERRIDES",
    "DECLARES",
    "DEFINES",
    "REGISTERS",
    "CONFIGURES",
    "CONTAINS",   # chunk → symbol
    "AFFECTS",    # chunk → file (transitive)
    "RENAMED_FROM",
    "RENAMED_TO",
]

ImpactDistance = Literal["DIRECT", "TRANSITIVE", "POSSIBLE", "TEXTUAL_ONLY", "UNRESOLVED"]
EvidenceSource = Literal["serena", "ast", "import_parse", "text_search", "heuristic", "unresolved"]
ImpactNodeType = Literal["symbol", "file", "change_chunk"]

ChangeStatus = Literal["added", "modified", "deleted", "renamed", "unchanged"]


# ── Nodes ─────────────────────────────────────────────────────────────────────

class ImpactNode(BaseModel):
    """A node in the semantic impact graph (symbol, file, or change chunk)."""
    id: str                              # canonical stable ID
    node_type: ImpactNodeType
    name: str
    kind: Optional[str] = None           # function | class | method | variable | import | module | file
    file: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    change_status: Optional[ChangeStatus] = None
    data: Dict[str, Any] = Field(default_factory=dict)


# ── Edges ─────────────────────────────────────────────────────────────────────

class ImpactEdge(BaseModel):
    """A directed edge in the semantic impact graph."""
    id: str
    source: str                          # ImpactNode.id
    target: str                          # ImpactNode.id
    relationship: RelationshipType
    distance: ImpactDistance = "DIRECT"
    confidence: float = 1.0
    evidence_source: EvidenceSource = "unresolved"
    evidence_detail: str = ""            # e.g. "serena.find_referencing_symbols"
    data: Dict[str, Any] = Field(default_factory=dict)


# ── Chunk impact summary ──────────────────────────────────────────────────────

class ChunkImpact(BaseModel):
    """Impact summary for a single diff chunk."""
    chunk_id: str
    file: str
    changed_lines: List[int] = Field(default_factory=list)   # [start, end]
    changed_symbols: List[str] = Field(default_factory=list) # ImpactNode.id list
    direct_affected: List[str] = Field(default_factory=list) # ImpactNode.id list — depth 1
    transitive_affected: List[str] = Field(default_factory=list)  # depth 2+
    handled_affected: List[str] = Field(default_factory=list)    # ImpactNode.id list — modified in diff
    unattended_affected: List[str] = Field(default_factory=list) # ImpactNode.id list — untouched in diff


# ── Top-level graph ───────────────────────────────────────────────────────────

class SemanticImpactGraph(BaseModel):
    """
    Full semantic impact graph for a change set (one git diff analysis).

    nodes  — deduplicated set of all symbols/files/chunks
    edges  — deduplicated set of all relationships
    chunks — per-chunk impact summaries (maps chunk_id → ChunkImpact)
    """
    change_set_id: str
    repository: str
    max_depth_used: int = 2
    nodes: List[ImpactNode] = Field(default_factory=list)
    edges: List[ImpactEdge] = Field(default_factory=list)
    chunks: List[ChunkImpact] = Field(default_factory=list)

    # ── Query helpers (used by API layer) ────────────────────────────────────

    def get_node(self, node_id: str) -> Optional[ImpactNode]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def get_edges_from(self, node_id: str) -> List[ImpactEdge]:
        return [e for e in self.edges if e.source == node_id]

    def get_edges_to(self, node_id: str) -> List[ImpactEdge]:
        return [e for e in self.edges if e.target == node_id]

    def get_consumers(self, node_id: str) -> List[ImpactNode]:
        """Return nodes that depend on (reference/call/import) this node."""
        consumer_ids = {e.source for e in self.edges if e.target == node_id}
        return [n for n in self.nodes if n.id in consumer_ids]

    def get_dependencies(self, node_id: str) -> List[ImpactNode]:
        """Return nodes that this node depends on."""
        dep_ids = {e.target for e in self.edges if e.source == node_id}
        return [n for n in self.nodes if n.id in dep_ids]

    def changed_nodes(self) -> List[ImpactNode]:
        return [n for n in self.nodes if n.change_status in ("added", "modified", "deleted", "renamed")]

    def get_unattended_nodes(self) -> List[ImpactNode]:
        """Return symbols that depend on changed nodes but were NOT modified in this diff."""
        changed_ids = {n.id for n in self.changed_nodes()}
        unattended = []
        for n in self.nodes:
            if n.node_type == "symbol" and n.id not in changed_ids:
                is_flagged = bool(n.data.get("is_unattended"))
                has_dep_on_changed = any(
                    e.source == n.id and e.target in changed_ids and e.relationship in ("REFERENCES", "CALLS", "IMPLEMENTS")
                    for e in self.edges
                )
                if is_flagged or has_dep_on_changed:
                    unattended.append(n)
        return unattended

    def get_chunk_subgraph(self, chunk_id: Optional[str] = None, chunk_index: Optional[int] = None) -> "Optional[SemanticImpactGraph]":
        """
        Return a self-contained SemanticImpactGraph scoped to a single diff chunk.

        Resolves the chunk by:
        - ``chunk_id``    — exact match on ChunkImpact.chunk_id
        - ``chunk_index`` — 0-based position in self.chunks (may differ from the
                            numeric suffix in chunk_id when chunks are de-duplicated)

        The returned graph contains only the nodes that belong to the chunk's
        blast radius (chunk node, file node, changed symbols, direct/transitive/
        handled/unattended affected) and only the edges whose both endpoints are
        inside that set.  The ``chunks`` list of the returned graph holds just the
        one matching ChunkImpact.

        Returns ``None`` when the chunk cannot be found.
        """
        chunk: Optional[ChunkImpact] = None
        if chunk_id is not None:
            for c in self.chunks:
                if c.chunk_id == chunk_id:
                    chunk = c
                    break
        elif chunk_index is not None:
            if 0 <= chunk_index < len(self.chunks):
                chunk = self.chunks[chunk_index]

        if chunk is None:
            return None

        # Collect all node IDs that belong to this chunk's subgraph
        relevant_ids: set[str] = set()
        relevant_ids.add(chunk.chunk_id)

        # Resolve the file node from edges (chunk → file via CONTAINS)
        for e in self.edges:
            if e.source == chunk.chunk_id and e.relationship == "CONTAINS":
                tgt = self.get_node(e.target)
                if tgt and tgt.node_type == "file":
                    relevant_ids.add(e.target)

        # Add all the categorised node ID lists from the ChunkImpact summary
        for nid in (
            chunk.changed_symbols
            + chunk.direct_affected
            + chunk.transitive_affected
            + chunk.handled_affected
            + chunk.unattended_affected
        ):
            relevant_ids.add(nid)

        # Retain only edges where both source AND target are in the relevant set
        sub_edges = [
            e for e in self.edges
            if e.source in relevant_ids and e.target in relevant_ids
        ]
        sub_nodes = [n for n in self.nodes if n.id in relevant_ids]

        return SemanticImpactGraph(
            change_set_id=self.change_set_id,
            repository=self.repository,
            max_depth_used=self.max_depth_used,
            nodes=sub_nodes,
            edges=sub_edges,
            chunks=[chunk],
        )

    def to_cytoscape(self) -> Dict[str, Any]:
        """
        Serialise to Cytoscape.js elements format:
        {"elements": {"nodes": [...], "edges": [...]}}
        Each node/edge carries its data dict with display fields.
        """
        unattended_ids = {n.id for n in self.get_unattended_nodes()}
        cy_nodes = []
        for n in self.nodes:
            status = n.change_status or "unchanged"
            is_unattended = (n.id in unattended_ids) or bool(n.data.get("is_unattended"))
            cy_nodes.append({
                "data": {
                    "id": n.id,
                    "label": n.name,
                    "nodeType": n.node_type,
                    "kind": n.kind or "",
                    "file": n.file or "",
                    "startLine": n.start_line or 0,
                    "endLine": n.end_line or 0,
                    "changeStatus": status,
                    "isUnattended": is_unattended,
                    "omissionRisk": n.data.get("omission_risk", "high" if is_unattended else "none"),
                    **n.data,
                }
            })
        cy_edges = []
        for e in self.edges:
            cy_edges.append({
                "data": {
                    "id": e.id,
                    "source": e.source,
                    "target": e.target,
                    "relationship": e.relationship,
                    "distance": e.distance,
                    "confidence": round(e.confidence, 3),
                    "evidenceSource": e.evidence_source,
                    "evidenceDetail": e.evidence_detail,
                }
            })
        return {"elements": {"nodes": cy_nodes, "edges": cy_edges}}
