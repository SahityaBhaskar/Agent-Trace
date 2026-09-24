from typing import List, Dict, Any, Optional
from ..models.session import SessionEvent, EpistemicStatus
from ..models.graph import CausalNode, CausalEdge, CausalGraphData
from ..models.report import DiffHunk, LogicalChange, AttentionItem, GroundedConcept
from .jev_client import jev_client

class CausalEngine:
    """Causal Correlation Engine: Links Agent Actions -> Observations -> Decisions -> Diff Hunks -> Risk Impact."""

    def build_causal_graph(
        self,
        user_prompt: str,
        events: List[SessionEvent],
        changes: List[LogicalChange],
        attention_items: Optional[List[AttentionItem]] = None,
        grounded_concept: Optional[GroundedConcept] = None,
    ) -> CausalGraphData:
        nodes: List[CausalNode] = []
        edges: List[CausalEdge] = []

        total_hunks = sum(len(ch.hunks) for ch in changes)

        # 1. Root User Request Node (Column 1: Discovery)
        req_node_id = "node_user_prompt"
        nodes.append(CausalNode(
            id=req_node_id,
            type="USER_REQUEST",
            title=f'Prompt: "{user_prompt[:50]}..."' if len(user_prompt) > 50 else f'Prompt: "{user_prompt}"',
            subtitle="Triggered session / diff target",
            epistemic_status="DECLARED",
            confidence_score=1.0,
            data={"details": user_prompt}
        ))

        # 2. Key Investigation Nodes (Column 1: Discovery)
        last_investigation_id = None
        if events:
            for evt in events:
                if evt.action_type in ("REPOSITORY_SEARCH", "SYMBOL_SEARCH", "FILE_READ"):
                    inv_id = f"node_inv_{evt.event_id}"
                    query = evt.target.query or evt.target.file_path or "code search"
                    nodes.append(CausalNode(
                        id=inv_id,
                        type="AGENT_INVESTIGATION",
                        title=f"{evt.action_type}: {query[:30]}",
                        subtitle=evt.payload.output_summary[:40] if evt.payload else f"Observed event #{evt.sequence_number}",
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
        else:
            # Synthesize git & AST parser investigation node for live repo mode
            inv_id = "node_inv_git_ast"
            nodes.append(CausalNode(
                id=inv_id,
                type="AGENT_INVESTIGATION",
                title="Git Diff & AST Parser",
                subtitle=f"Parsed {total_hunks} hunk(s) across working tree",
                epistemic_status="OBSERVED",
                confidence_score=1.0,
                data={"source": "live_git_engine"}
            ))
            edges.append(CausalEdge(
                id=f"e_{req_node_id}_{inv_id}",
                source=req_node_id,
                target=inv_id,
                relation="MOTIVATED_BY",
                confidence=0.99
            ))
            last_investigation_id = inv_id

        # 3. Architectural Decision Nodes (Column 2: Decisions)
        first_hunk_id = None
        for idx, change in enumerate(changes):
            dec_id = f"node_dec_{change.id}"
            nodes.append(CausalNode(
                id=dec_id,
                type="ARCHITECTURAL_DECISION",
                title=f"Jev {change.category}",
                subtitle=f"{change.title[:40]} ({int(change.confidence_score * 100)}% conf)",
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
                confidence=0.96
            ))

            # 4. Code Hunk Nodes (Column 3: Code Hunks)
            for hunk in change.hunks:
                hunk_node_id = f"node_hunk_{hunk.id}"
                if not first_hunk_id:
                    first_hunk_id = hunk_node_id

                fname = hunk.file_path.split("/")[-1] if hunk.file_path else "file"
                sym = hunk.symbol if hunk.symbol and hunk.symbol != "unknown" else fname

                nodes.append(CausalNode(
                    id=hunk_node_id,
                    type="CODE_HUNK" if hunk.change_type == "MODIFIED" else "LOGICAL_CHANGE",
                    title=f"{sym}",
                    subtitle=f"{fname} ({hunk.line_range})",
                    epistemic_status="OBSERVED",
                    confidence_score=hunk.jev_result.classification.confidence,
                    data={
                        "file_path": hunk.file_path,
                        "line_range": hunk.line_range,
                        "jev_category": hunk.jev_result.classification.category,
                        "blast_score": hunk.jev_result.blast_radius.score,
                    }
                ))
                edges.append(CausalEdge(
                    id=f"e_{dec_id}_{hunk_node_id}",
                    source=dec_id,
                    target=hunk_node_id,
                    relation="INTRODUCED" if hunk.change_type == "ADDED" else "MODIFIES",
                    confidence=0.96
                ))

        # 5. Risk & Learning Concept Nodes (Column 4: Impact & Risk)
        hunk_target = first_hunk_id or (nodes[-1].id if nodes else req_node_id)

        if attention_items:
            for a_idx, item in enumerate(attention_items):
                risk_node_id = f"node_risk_{a_idx}"
                nodes.append(CausalNode(
                    id=risk_node_id,
                    type="RISK_FLAG",
                    title=item.title[:38],
                    subtitle=f"Jev {item.level} Risk ({int(item.jev_attention_probability * 100)}% prob)",
                    epistemic_status="OBSERVED",
                    confidence_score=item.jev_attention_probability,
                    data={"action_required": item.action_required, "detail": item.detail}
                ))
                edges.append(CausalEdge(
                    id=f"e_{hunk_target}_{risk_node_id}",
                    source=hunk_target,
                    target=risk_node_id,
                    relation="CREATES_RISK",
                    confidence=item.jev_attention_probability
                ))

        if grounded_concept:
            concept_node_id = "node_grounded_concept"
            nodes.append(CausalNode(
                id=concept_node_id,
                type="LEARNING_CONCEPT",
                title=f"Concept: {grounded_concept.name[:30]}",
                subtitle=grounded_concept.headline[:40],
                epistemic_status="INFERRED",
                confidence_score=0.95,
                data={"what_it_is": grounded_concept.what_it_is}
            ))
            edges.append(CausalEdge(
                id=f"e_{hunk_target}_{concept_node_id}",
                source=hunk_target,
                target=concept_node_id,
                relation="GROUNDED_IN",
                confidence=0.95
            ))

        return CausalGraphData(nodes=nodes, edges=edges)

causal_engine = CausalEngine()
