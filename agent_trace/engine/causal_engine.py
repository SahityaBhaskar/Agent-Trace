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
        _last_inv_node = None
        pending_reasoning = []
        if events:
            for evt in events:
                if evt.action_type == "AGENT_REASONING_NOTE":
                    # Capture verbatim agent reasoning
                    reasoning_text = (
                        (evt.target.query if evt.target else "") or
                        (evt.payload.output_summary if evt.payload else "") or ""
                    ).strip()
                    if reasoning_text:
                        pending_reasoning.append(reasoning_text)
                    continue  # don't emit a separate node for reasoning events

                if evt.action_type in ("REPOSITORY_SEARCH", "SYMBOL_SEARCH", "FILE_READ"):
                    inv_id = f"node_inv_{evt.event_id}"
                    query = (evt.target.query if evt.target else "") or (evt.target.file_path if evt.target else "") or ""

                    # Extract tool name from the payload summary ("tool_name: {args}")
                    raw_summary = (evt.payload.output_summary or "") if evt.payload else ""
                    tool_name_from_payload = raw_summary.split(":")[0].strip() if ":" in raw_summary else ""

                    # Build a readable title: prefer "tool_name: query", fall back to action_type
                    if query:
                        title = f"{tool_name_from_payload or evt.action_type}: {query}"
                    elif tool_name_from_payload:
                        title = tool_name_from_payload
                    else:
                        title = evt.action_type

                    # Build a readable subtitle: strip empty-arg noise like "tool: {}" or "tool: {}"
                    if raw_summary and raw_summary != f"{tool_name_from_payload}: {{}}":
                        subtitle = raw_summary
                    elif query:
                        subtitle = query
                    else:
                        subtitle = f"Observed event #{evt.sequence_number}"

                    node_data = {"event_id": evt.event_id, "timestamp": evt.timestamp, "action_type": evt.action_type}
                    if pending_reasoning:
                        node_data["reasoning"] = "\n\n".join(pending_reasoning)
                        node_data["reasoning_epistemic"] = "DECLARED"
                        pending_reasoning = []
                    new_node = CausalNode(
                        id=inv_id,
                        type="AGENT_INVESTIGATION",
                        title=title,
                        subtitle=subtitle,
                        epistemic_status="OBSERVED",
                        confidence_score=0.98,
                        data=node_data
                    )
                    nodes.append(new_node)
                    edges.append(CausalEdge(
                        id=f"e_{req_node_id}_{inv_id}",
                        source=req_node_id,
                        target=inv_id,
                        relation="MOTIVATED_BY",
                        confidence=0.99 if events else 1.0
                    ))
                    last_investigation_id = inv_id
                    _last_inv_node = new_node

            # If trailing reasoning notes exist, attach them to the last investigation node
            if pending_reasoning and _last_inv_node is not None:
                existing = _last_inv_node.data.get("reasoning", "")
                trailing = "\n\n".join(pending_reasoning)
                _last_inv_node.data["reasoning"] = (
                    f"{existing}\n\n{trailing}".strip() if existing else trailing
                )
                _last_inv_node.data["reasoning_epistemic"] = "DECLARED"

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
                confidence=1.0
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
                confidence=round(change.confidence_score, 2)
            ))

            # 4. Changed File Nodes (Column 3: Grouped by File rather than symbols)
            from collections import OrderedDict
            file_hunks_map: Dict[str, List[DiffHunk]] = OrderedDict()
            for hunk in change.hunks:
                fpath = hunk.file_path or "unknown_file"
                file_hunks_map.setdefault(fpath, []).append(hunk)

            for file_path, f_hunks in file_hunks_map.items():
                first_h = f_hunks[0]
                file_node_id = f"node_file_{first_h.id}"
                if not first_hunk_id:
                    first_hunk_id = file_node_id

                fname = file_path.split("/")[-1] if file_path else "file"
                symbols = [h.symbol for h in f_hunks if h.symbol and h.symbol != "unknown"]
                sym_str = ", ".join(symbols[:3])
                if len(symbols) > 3:
                    sym_str += f" +{len(symbols)-3}"

                hunk_count_str = f"{len(f_hunks)} hunk{'s' if len(f_hunks) != 1 else ''}"
                subtitle = f"{hunk_count_str} · {sym_str}" if sym_str else f"{hunk_count_str} ({first_h.line_range})"

                # Aggregate stats for this file
                max_blast = max((h.jev_result.blast_radius.score for h in f_hunks if h.jev_result and h.jev_result.blast_radius), default=1.0)
                conf_list = [h.jev_result.classification.confidence for h in f_hunks if h.jev_result and h.jev_result.classification]
                avg_conf = (sum(conf_list) / len(conf_list)) if conf_list else 0.9
                primary_cat = first_h.jev_result.classification.category if first_h.jev_result and first_h.jev_result.classification else change.category

                nodes.append(CausalNode(
                    id=file_node_id,
                    type="CODE_HUNK" if any(h.change_type == "MODIFIED" for h in f_hunks) else "LOGICAL_CHANGE",
                    title=f"{fname}",
                    subtitle=subtitle,
                    epistemic_status="OBSERVED",
                    confidence_score=round(avg_conf, 2),
                    data={
                        "file_path": file_path,
                        "hunk_count": len(f_hunks),
                        "symbols": symbols,
                        "line_ranges": [h.line_range for h in f_hunks if h.line_range],
                        "hunk_ids": [h.id for h in f_hunks],
                        "jev_category": primary_cat,
                        "blast_score": max_blast,
                        "provenance": "git_diff_observed" if first_h.jev_result and first_h.jev_result.source == "live_api" else "calibrated_engine",
                        "jev_source": first_h.jev_result.source if first_h.jev_result else "calibrated",
                    }
                ))
                edges.append(CausalEdge(
                    id=f"e_{dec_id}_{file_node_id}",
                    source=dec_id,
                    target=file_node_id,
                    relation="INTRODUCED" if all(h.change_type == "ADDED" for h in f_hunks) else "MODIFIES",
                    confidence=round(avg_conf, 2)
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
                    data={"action_required": item.action_required, "detail": item.detail, "provenance": "jev_risk_engine", "risk_index": a_idx}
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
            _concept_epistemic = "OBSERVED" if changes and any(
                h.jev_result.source == "live_api" for ch in changes for h in ch.hunks
            ) else "INFERRED"
            nodes.append(CausalNode(
                id=concept_node_id,
                type="LEARNING_CONCEPT",
                title=f"Concept: {grounded_concept.name[:30]}",
                subtitle=grounded_concept.headline[:40],
                epistemic_status=_concept_epistemic,
                confidence_score=0.95,
                data={"what_it_is": grounded_concept.what_it_is, "provenance": "jev_classification_inferred"}
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
