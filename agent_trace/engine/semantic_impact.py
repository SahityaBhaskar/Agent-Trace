"""
SemanticImpactGraphBuilder — builds the full semantic impact graph for a diff.

Pipeline
--------
For every DiffHunk in a change set:
  1.  DiffSymbolMapper  → identify changed symbols (Serena / AST / heuristic)
  2.  For each changed symbol:
        a. find_referencing_symbols  → who calls/uses this? (reverse impact)
        b. Repo graph CALLS edges    → what does this call? (forward deps)
        c. Import analysis           → file-level import relationships
  3.  Traverse consumers up to maxDepth
  4.  Deduplicate nodes + edges by canonical ID
  5.  Classify confidence + evidence on every edge
  6.  Return SemanticImpactGraph

Design constraints
------------------
- Never raise into callers; degrade gracefully on Serena failures
- Visited set prevents infinite cycles
- maxDepth caps traversal (default 2)
- Serena is queried per-symbol (targeted), not for the whole repo
- Textual matches are never promoted to semantic references
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional, Set

from ..models.impact_graph import (
    ChunkImpact,
    EvidenceSource,
    ImpactEdge,
    ImpactNode,
    SemanticImpactGraph,
)
from ..models.report import DiffHunk
from .diff_symbol_mapper import (
    DiffSymbolMapper,
    _make_file_id,
    _make_symbol_id,
    CONF_SERENA,
    CONF_AST,
    CONF_HEURISTIC,
)
from .repo_graph import get_repo_graph, RepoGraph
from .serena_client import SerenaClient, get_serena_client

log = logging.getLogger(__name__)

# ── Edge confidence values ────────────────────────────────────────────────────
CONF_SERENA_REF   = 0.98
CONF_SERENA_DECL  = 0.98
CONF_REPO_GRAPH   = 0.85   # AST-derived call graph
CONF_IMPORT_PARSE = 0.90
CONF_HEURISTIC_E  = 0.30


def _edge_id(source: str, target: str, rel: str) -> str:
    """Stable deterministic edge ID."""
    raw = f"{source}|{rel}|{target}"
    return "e_" + hashlib.md5(raw.encode()).hexdigest()[:12]


def _node_label(n: ImpactNode) -> str:
    return n.name.split("::")[-1] if "::" in n.name else n.name


class SemanticImpactGraphBuilder:
    """
    Builds a SemanticImpactGraph from a list of DiffHunks.

    Parameters
    ----------
    serena      : SerenaClient (or None → all fallback)
    repo_path   : absolute path to the repository root
    max_depth   : traversal depth for transitive consumers (default 2)
    """

    def __init__(
        self,
        serena: Optional[SerenaClient] = None,
        repo_path: str = ".",
        max_depth: int = 2,
        max_serena_calls: int = 8,
    ):
        self._serena = serena or get_serena_client()
        self._repo_path = repo_path
        self._max_depth = max_depth
        # Cap total find_referencing_symbols calls per build to avoid cascading
        # timeouts when a diff has many hunks or when Serena is slow to respond.
        self._max_serena_calls = max_serena_calls
        self._mapper = DiffSymbolMapper(serena=self._serena)

        # Mutable graph accumulators (reset per build call)
        self._nodes: Dict[str, ImpactNode] = {}
        self._edges: Dict[str, ImpactEdge] = {}
        self._visited_refs: Set[str] = set()
        self._serena_call_count: int = 0

    # ── Public entry point ────────────────────────────────────────────────────

    def build(
        self,
        hunks: List[DiffHunk],
        change_set_id: str,
        repository: str,
    ) -> SemanticImpactGraph:
        """Build and return the full SemanticImpactGraph for a list of DiffHunks."""
        # Reset state
        self._nodes = {}
        self._edges = {}
        self._visited_refs = set()
        self._serena_call_count = 0

        # Activate Serena on the repo (best-effort)
        if self._serena:
            try:
                self._serena.activate_project(self._repo_path)
            except Exception as e:
                log.warning("Serena project activation failed: %s", e)

        # Load repo graph for fallback CALLS edges
        try:
            repo_graph = get_repo_graph(self._repo_path)
        except Exception:
            repo_graph = None

        # Upfront: extract all changed symbol IDs and names across all hunks
        all_changed_sym_ids: Set[str] = set()
        for c_idx, h in enumerate(hunks):
            s_line, e_line = self._parse_line_range(h.line_range)
            c_nodes = self._mapper.map_chunk_to_symbols(
                file_path=h.file_path,
                start_line=s_line,
                end_line=e_line,
                change_status=h.change_type.lower(),
                change_set_id=change_set_id,
                chunk_index=c_idx,
                repo_root=self._repo_path,
            )
            for sn in c_nodes:
                all_changed_sym_ids.add(sn.id)
                all_changed_sym_ids.add(sn.name)
            if h.symbol:
                all_changed_sym_ids.add(_make_symbol_id(h.file_path, h.symbol))
                all_changed_sym_ids.add(h.symbol)

        chunk_impacts: List[ChunkImpact] = []

        for chunk_idx, hunk in enumerate(hunks):
            ci = self._process_hunk(hunk, chunk_idx, change_set_id, repo_graph, all_changed_sym_ids)
            chunk_impacts.append(ci)

        return SemanticImpactGraph(
            change_set_id=change_set_id,
            repository=repository,
            max_depth_used=self._max_depth,
            nodes=list(self._nodes.values()),
            edges=list(self._edges.values()),
            chunks=chunk_impacts,
        )

    # ── Per-hunk processing ───────────────────────────────────────────────────

    def _process_hunk(
        self,
        hunk: DiffHunk,
        chunk_idx: int,
        change_set_id: str,
        repo_graph: Optional[RepoGraph],
        all_changed_ids: Optional[Set[str]] = None,
    ) -> ChunkImpact:
        file_path = hunk.file_path
        change_status = hunk.change_type.lower()  # "added"|"modified"|"deleted"
        active_changed_ids = all_changed_ids or set()

        # Parse line range from hunk (e.g. "Lines 10–45")
        start_line, end_line = self._parse_line_range(hunk.line_range)

        # ── Chunk node ────────────────────────────────────────────────────────
        chunk_node = self._mapper.make_chunk_node(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            change_set_id=change_set_id,
            chunk_index=chunk_idx,
            change_status=change_status,
        )
        self._add_node(chunk_node)

        # ── File node ─────────────────────────────────────────────────────────
        file_node = self._mapper.make_file_node(file_path, change_status)
        self._add_node(file_node)
        self._add_edge(chunk_node.id, file_node.id, "CONTAINS", "DIRECT", CONF_IMPORT_PARSE, "ast", "chunk→file")

        # ── Changed symbol nodes ──────────────────────────────────────────────
        changed_sym_nodes = self._mapper.map_chunk_to_symbols(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            change_status=change_status,
            change_set_id=change_set_id,
            chunk_index=chunk_idx,
            repo_root=self._repo_path,
        )
        if (not changed_sym_nodes or all(n.kind == "unresolved" for n in changed_sym_nodes)) and hunk.symbol and hunk.symbol != "module":
            changed_sym_nodes = [
                ImpactNode(
                    id=_make_symbol_id(file_path, hunk.symbol),
                    node_type="symbol",
                    name=hunk.symbol,
                    kind="function",
                    file=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    change_status=change_status,
                    data={"evidence_source": "hunk_metadata", "confidence": CONF_AST},
                )
            ]
        changed_sym_ids: List[str] = []
        for sym_node in changed_sym_nodes:
            self._add_node(sym_node)
            changed_sym_ids.append(sym_node.id)
            self._add_edge(chunk_node.id, sym_node.id, "CONTAINS", "DIRECT", CONF_IMPORT_PARSE, "ast", "chunk→symbol")
            self._add_edge(file_node.id, sym_node.id, "DEFINES", "DIRECT", CONF_IMPORT_PARSE, "ast", "file→symbol")

        # ── Semantic relationships for each changed symbol ────────────────────
        direct_affected: List[str] = []
        transitive_affected: List[str] = []
        handled_affected: List[str] = []
        unattended_affected: List[str] = []

        for sym_node in changed_sym_nodes:
            if sym_node.kind == "unresolved":
                continue
            d, t, h_aff, u_aff = self._discover_relationships(
                sym_node=sym_node,
                depth=0,
                repo_graph=repo_graph,
                change_set_id=change_set_id,
                all_changed_ids=active_changed_ids,
            )
            direct_affected.extend(d)
            transitive_affected.extend(t)
            handled_affected.extend(h_aff)
            unattended_affected.extend(u_aff)

        # ── File-level import relationships ───────────────────────────────────
        if repo_graph:
            self._add_import_edges(file_path, repo_graph)

        return ChunkImpact(
            chunk_id=chunk_node.id,
            file=file_path,
            changed_lines=[start_line, end_line],
            changed_symbols=changed_sym_ids,
            direct_affected=list(set(direct_affected)),
            transitive_affected=list(set(transitive_affected)),
            handled_affected=list(set(handled_affected)),
            unattended_affected=list(set(unattended_affected)),
        )

    # ── Relationship discovery ────────────────────────────────────────────────

    def _discover_relationships(
        self,
        sym_node: ImpactNode,
        depth: int,
        repo_graph: Optional[RepoGraph],
        change_set_id: str,
        all_changed_ids: Optional[Set[str]] = None,
    ) -> tuple[List[str], List[str], List[str], List[str]]:
        """
        Discover and add edges for a symbol.
        Returns (direct_ids, transitive_ids, handled_ids, unattended_ids).
        """
        if depth > self._max_depth:
            return [], [], [], []
        visit_key = f"{sym_node.id}@{depth}"
        if visit_key in self._visited_refs:
            return [], [], [], []
        self._visited_refs.add(visit_key)

        direct_ids: List[str] = []
        transitive_ids: List[str] = []
        handled_ids: List[str] = []
        unattended_ids: List[str] = []
        changed_filter = all_changed_ids or set()

        # ── A. Serena: reverse references (who uses this symbol) ──────────────
        serena_refs = self._serena_referencing(sym_node)
        for ref_node, confidence in serena_refs:
            is_handled = (
                ref_node.id in changed_filter or
                ref_node.name in changed_filter or
                (ref_node.file and any(f in changed_filter for f in [ref_node.file, f"{ref_node.file}::{ref_node.name}"]))
            )
            if is_handled:
                ref_node.change_status = "modified"
                handled_ids.append(ref_node.id)
            else:
                ref_node.change_status = "unchanged"
                ref_node.data["is_unattended"] = True
                is_test = "test" in (ref_node.file or "").lower() or "test" in ref_node.name.lower()
                ref_node.data["omission_risk"] = "medium_test" if is_test else "high"
                unattended_ids.append(ref_node.id)

            self._add_node(ref_node)
            self._add_edge(
                ref_node.id, sym_node.id,
                "REFERENCES", "DIRECT",
                confidence, "serena",
                "serena.find_referencing_symbols",
            )
            if depth == 0:
                direct_ids.append(ref_node.id)

            # Traverse consumers transitively
            if depth < self._max_depth:
                d2, t2, h2, u2 = self._discover_relationships(
                    ref_node, depth + 1, repo_graph, change_set_id, all_changed_ids
                )
                transitive_ids.extend(d2)
                transitive_ids.extend(t2)
                handled_ids.extend(h2)
                unattended_ids.extend(u2)
                if depth > 0:
                    transitive_ids.append(ref_node.id)

        # ── B. Repo graph: CALLS edges (forward deps from this symbol) ────────
        if repo_graph:
            callee_ids = self._repo_graph_calls(sym_node, repo_graph)
            for callee_id in callee_ids:
                if callee_id in self._nodes:
                    self._add_edge(
                        sym_node.id, callee_id,
                        "CALLS", "DIRECT",
                        CONF_REPO_GRAPH, "ast",
                        "repo_graph.CALLS",
                    )

            # C. Callers of this symbol via repo graph (fallback when Serena empty)
            if not serena_refs:
                caller_ids = self._repo_graph_callers(sym_node, repo_graph)
                for caller_id in caller_ids:
                    if caller_id in self._nodes:
                        self._add_edge(
                            caller_id, sym_node.id,
                            "CALLS", "DIRECT",
                            CONF_REPO_GRAPH, "ast",
                            "repo_graph.callers_of",
                        )
                        if depth == 0:
                            direct_ids.append(caller_id)
                            if caller_id in changed_filter:
                                handled_ids.append(caller_id)
                            else:
                                unattended_ids.append(caller_id)
                                node_obj = self._nodes[caller_id]
                                node_obj.data["is_unattended"] = True
                                is_test = "test" in (node_obj.file or "").lower() or "test" in node_obj.name.lower()
                                node_obj.data["omission_risk"] = "medium_test" if is_test else "high"

        return direct_ids, transitive_ids, handled_ids, unattended_ids

    # ── Serena helpers ────────────────────────────────────────────────────────

    def _serena_referencing(self, sym_node: ImpactNode) -> List[tuple[ImpactNode, float]]:
        """Call Serena find_referencing_symbols and return (ImpactNode, confidence) pairs."""
        if not self._serena:
            return []
        # Hard cap: don't fire unbounded Serena calls per build
        if self._serena_call_count >= self._max_serena_calls:
            log.debug("Serena call cap (%d) reached, skipping %s", self._max_serena_calls, sym_node.name)
            return []
        self._serena_call_count += 1
        try:
            raw = self._serena.find_referencing_symbols(
                name_path=sym_node.name,
                relative_path=sym_node.file,
            )
            if not raw:
                return []
            refs = SerenaClient.parse_referencing_symbols(raw)
            results = []
            for r in refs:
                ref_file = r.get("file", "")
                ref_name = r.get("name", "unknown")
                if not ref_name or ref_name == sym_node.name:
                    continue
                node = ImpactNode(
                    id=_make_symbol_id(ref_file, ref_name),
                    node_type="symbol",
                    name=ref_name,
                    kind=r.get("kind", "symbol"),
                    file=ref_file,
                    start_line=r.get("line"),
                    change_status="unchanged",
                    data={"evidence_source": "serena", "confidence": CONF_SERENA_REF},
                )
                results.append((node, CONF_SERENA_REF))
            return results
        except Exception as e:
            log.debug("Serena find_referencing_symbols failed for %s: %s", sym_node.name, e)
            return []

    # ── Repo graph helpers ────────────────────────────────────────────────────

    @staticmethod
    def _repo_graph_calls(sym_node: ImpactNode, repo_graph: RepoGraph) -> List[str]:
        """Return node IDs that sym_node CALLS (forward dependencies)."""
        sym_name = sym_node.name.split(".")[-1]
        result = []
        for edge in repo_graph.edges:
            if edge.relation == "CALLS" and (
                edge.source.endswith(f":{sym_name}") or
                edge.source.endswith(f":{sym_node.name}")
            ):
                target_name = edge.target.split(":")[-1]
                target_file = edge.target.split(":")[1] if ":" in edge.target else ""
                result.append(_make_symbol_id(target_file, target_name))
        return result

    @staticmethod
    def _repo_graph_callers(sym_node: ImpactNode, repo_graph: RepoGraph) -> List[str]:
        """Return node IDs that CALL sym_node (reverse from repo graph)."""
        caller_raw_ids = repo_graph.callers_of(sym_node.name)
        results = []
        for raw_id in caller_raw_ids:
            # raw_id like "func:path/to/file.py:FunctionName"
            parts = raw_id.split(":")
            if len(parts) >= 3:
                caller_file = parts[1]
                caller_name = parts[2]
                results.append(_make_symbol_id(caller_file, caller_name))
        return results

    def _add_import_edges(self, file_path: str, repo_graph: RepoGraph):
        """Add IMPORTS edges for the changed file from the repo graph."""
        imported_ids = repo_graph.imports_of(file_path)
        file_node_id = _make_file_id(file_path)
        for imp_id in imported_ids:
            # imp_id like "file:path/to/module.py"
            imp_path = imp_id.replace("file:", "", 1)
            imp_node = ImpactNode(
                id=imp_id,
                node_type="file",
                name=imp_path.split("/")[-1],
                kind="file",
                file=imp_path,
                change_status="unchanged",
            )
            self._add_node(imp_node)
            self._add_edge(
                file_node_id, imp_id,
                "IMPORTS", "DIRECT",
                CONF_IMPORT_PARSE, "import_parse",
                "repo_graph.imports_of",
            )

    # ── Graph mutation helpers ────────────────────────────────────────────────

    def _add_node(self, node: ImpactNode):
        """Add node if not already present (dedup by id). Merge status/flags if already present."""
        if node.id not in self._nodes:
            self._nodes[node.id] = node
        else:
            existing = self._nodes[node.id]
            if existing.change_status in ("unchanged", None) and node.change_status in ("added", "modified", "deleted", "renamed"):
                existing.change_status = node.change_status
            if node.data.get("is_unattended"):
                existing.data["is_unattended"] = True
                if "omission_risk" in node.data:
                    existing.data["omission_risk"] = node.data["omission_risk"]

    def _add_edge(
        self,
        source: str,
        target: str,
        relationship: str,
        distance: str,
        confidence: float,
        evidence_source: str,
        evidence_detail: str,
    ):
        """Add edge if not already present (dedup by source+rel+target)."""
        if source == target:
            return
        eid = _edge_id(source, target, relationship)
        if eid not in self._edges:
            self._edges[eid] = ImpactEdge(
                id=eid,
                source=source,
                target=target,
                relationship=relationship,
                distance=distance,
                confidence=round(confidence, 3),
                evidence_source=evidence_source,
                evidence_detail=evidence_detail,
            )

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_line_range(line_range: str) -> tuple[int, int]:
        """
        Parse 'Lines 10–45' or 'Lines 10–10' into (10, 45).
        Falls back to (1, 1) on parse failure.
        """
        import re
        m = re.search(r"(\d+)[–\-](\d+)", line_range)
        if m:
            return int(m.group(1)), int(m.group(2))
        m2 = re.search(r"(\d+)", line_range)
        if m2:
            v = int(m2.group(1))
            return v, v
        return 1, 1


# ── Module-level builder (lazy singleton, reset per analysis) ─────────────────

_builder: Optional[SemanticImpactGraphBuilder] = None


def get_impact_graph_builder(
    repo_path: str = ".",
    max_depth: int = 2,
) -> SemanticImpactGraphBuilder:
    """
    Return a SemanticImpactGraphBuilder configured for the given repo.
    Creates a new instance whenever repo_path changes.
    """
    global _builder
    if _builder is None or _builder._repo_path != repo_path:
        _builder = SemanticImpactGraphBuilder(
            serena=get_serena_client(),
            repo_path=repo_path,
            max_depth=max_depth,
        )
    return _builder
