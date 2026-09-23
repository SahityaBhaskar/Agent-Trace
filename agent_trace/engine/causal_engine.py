from typing import List, Dict, Any, Optional
from ..models.session import SessionEvent, EpistemicStatus
from ..models.graph import CausalNode, CausalEdge, CausalGraphData
from ..models.report import DiffHunk, LogicalChange
from .jev_client import jev_client

class CausalEngine:
    """Causal Correlation Engine: Links Agent Actions -> Observations -> Decisions -> Diff Hunks."""

    def build_causal_graph(
        self,
        user_prompt: str,
        events: List[SessionEvent],
        changes: List[LogicalChange]
    ) -> CausalGraphData:
        nodes: List[CausalNode] = []
        edges: List[CausalEdge] = []

        # 1. Root User Request Node
        req_node_id = "node_user_prompt"
        nodes.append(CausalNode(
            id=req_node_id,
            type="USER_REQUEST",
            title=f'Prompt: "{user_prompt}"',
            subtitle="Triggered session",
            epistemic_status="DECLARED",
            confidence_score=1.0,
            data={"details": user_prompt}
        ))

        # 2. Key Investigation Nodes (Searches & Reads)
        last_investigation_id = None
        for evt in events:
            if evt.action_type in ("REPOSITORY_SEARCH", "SYMBOL_SEARCH"):
                inv_id = f"node_inv_{evt.event_id}"
                query = evt.target.query if evt.target else "code search"
                nodes.append(CausalNode(
                    id=inv_id,
                    type="AGENT_INVESTIGATION",
                    title=f"grep: {query}",
                    subtitle=evt.payload.output_summary if evt.payload else f"Found matches in repo",
                    epistemic_status="OBSERVED",
                    confidence_score=0.98,
                    data={"event_id": evt.event_id, "timestamp": evt.timestamp}
                ))
                edges.append(CausalEdge(
                    id=f"e_{req_node_id}_{inv_id}",
                    source=req_node_id,
                    target=inv_id,
                    relation="MOTIVATED_BY",
                    confidence=0.99
                ))
                last_investigation_id = inv_id

        # 3. Architectural Decision Nodes
        for idx, change in enumerate(changes):
            dec_id = f"node_dec_{change.id}"
            nodes.append(CausalNode(
                id=dec_id,
                type="ARCHITECTURAL_DECISION",
                title=f"Decision: {change.title[:35]}...",
                subtitle=f"Jev {change.category} ({int(change.confidence_score * 100)}%)",
                epistemic_status=change.epistemic_status,
                confidence_score=change.confidence_score,
                data={"why": change.why_explanation}
            ))

            source_for_dec = last_investigation_id or req_node_id
            edges.append(CausalEdge(
                id=f"e_{source_for_dec}_{dec_id}",
                source=source_for_dec,
                target=dec_id,
                relation="DISCOVERED_IN",
                confidence=0.95
            ))

            # 4. Code Hunk Nodes
            for hunk in change.hunks:
                hunk_node_id = f"node_hunk_{hunk.id}"
                nodes.append(CausalNode(
                    id=hunk_node_id,
                    type="CODE_HUNK" if hunk.change_type == "MODIFIED" else "LOGICAL_CHANGE",
                    title=f"{hunk.symbol}",
                    subtitle=f"{hunk.file_path} ({hunk.line_range})",
                    epistemic_status="OBSERVED",
                    confidence_score=hunk.jev_result.classification.confidence,
                    data={
                        "file_path": hunk.file_path,
                        "line_range": hunk.line_range,
                        "jev_category": hunk.jev_result.classification.category
                    }
                ))
                edges.append(CausalEdge(
                    id=f"e_{dec_id}_{hunk_node_id}",
                    source=dec_id,
                    target=hunk_node_id,
                    relation="INTRODUCED" if hunk.change_type == "ADDED" else "MODIFIES",
                    confidence=0.96
                ))

        return CausalGraphData(nodes=nodes, edges=edges)

causal_engine = CausalEngine()
