import os
from typing import List, Set, Tuple, Optional, Dict, Any
from ..models.report import AttentionItem, LogicalChange
from .gemini_client import gemini_client

REVIEW_THRESHOLD = float(os.getenv("AGENTTRACE_REVIEW_THRESHOLD", "0.80"))

class RiskEngine:
    """Risk & Attention Engine: Filters blast radius and flags high-leverage review concerns."""

    def evaluate_changes(
        self,
        changes: List[LogicalChange],
        semantic_impact_graph: Optional[Any] = None,
    ) -> List[AttentionItem]:
        items: List[AttentionItem] = []

        for change in changes:
            # Track (rule, symbol, file) tuples to deduplicate within a change
            emitted: Set[Tuple[str, str, str]] = set()
            jev_item_count_before = len(items)

            for hunk in change.hunks:
                symbol = hunk.symbol or ""
                file = hunk.file_path or ""
                jev = hunk.jev_result

                # Clean representation for presentation
                clean_file = file.strip() if (file and file.strip() and file != "unknown_file") else "source file"
                is_module_level = not symbol or symbol == "module"
                display_symbol = f"module-level code in {clean_file}" if is_module_level else f"'{symbol}' in {clean_file}"
                title_symbol = f"Module ({clean_file})" if is_module_level else symbol

                # Extract diff snippet from hunk
                diff_lines = []
                if hunk.old_lines:
                    diff_lines.append(f"- {hunk.old_lines}")
                if hunk.new_lines:
                    diff_lines.append(f"+ {hunk.new_lines}")
                diff_snippet = "\n".join(diff_lines)

                # Extract change graph context from semantic impact graph & logical change
                affected_callers: List[str] = []
                unattended_callers: List[str] = []
                coupled_services: List[str] = list(change.affected_services or [])

                if semantic_impact_graph and getattr(semantic_impact_graph, "chunks", None):
                    for chunk in semantic_impact_graph.chunks:
                        if chunk.file == hunk.file_path:
                            affected_callers.extend(chunk.handled_affected or [])
                            unattended_callers.extend(chunk.unattended_affected or [])
                            break

                graph_context = {
                    "affected_callers": list(dict.fromkeys(affected_callers)),
                    "unattended_callers": list(dict.fromkeys(unattended_callers)),
                    "coupled_services": coupled_services,
                    "blast_radius_level": jev.blast_radius.level,
                    "blast_radius_score": jev.blast_radius.score,
                }

                # Rule 1 — High blast radius hunk
                if jev.blast_radius.score >= 3.5 or jev.blast_radius.level == "system_critical":
                    key = ("rule1", symbol, file)
                    if key not in emitted:
                        emitted.add(key)
                        raw_detail = (
                            f"Jev blast radius score {jev.blast_radius.score:.2f} "
                            f"(level: {jev.blast_radius.level}) exceeds safe threshold for {display_symbol}."
                        )
                        deep_analysis = gemini_client.synthesize_deep_risk_analysis(
                            rule="High Blast Radius Review",
                            symbol=symbol or file,
                            file_path=file,
                            diff_snippet=diff_snippet,
                            jev_category=jev.classification.category,
                            blast_level=jev.blast_radius.level,
                            blast_score=jev.blast_radius.score,
                            human_review_probability=jev.human_review.probability,
                            breaking_risk=jev.breaking_risk.is_breaking,
                            graph_context=graph_context,
                        )
                        items.append(AttentionItem(
                            title=f"High Blast Radius: {title_symbol}",
                            level="HIGH",
                            detail=raw_detail,
                            action_required="Run full integration test suite before merging.",
                            jev_attention_probability=min(jev.blast_radius.score / 5.0, 1.0),
                            llm_summary=deep_analysis.get("summary"),
                            symbol=symbol or file,
                            file_path=file,
                            hunk_id=hunk.id,
                            diff_snippet=diff_snippet,
                            graph_context=graph_context,
                            llm_analysis=deep_analysis,
                        ))

                # Rule 2 — Breaking change detected
                if jev.breaking_risk.is_breaking:
                    key = ("rule2", symbol, file)
                    if key not in emitted:
                        emitted.add(key)
                        raw_detail = f"Jev detected this change in {display_symbol} breaks existing callers or public contracts."
                        deep_analysis = gemini_client.synthesize_deep_risk_analysis(
                            rule="Breaking Change Review",
                            symbol=symbol or file,
                            file_path=file,
                            diff_snippet=diff_snippet,
                            jev_category=jev.classification.category,
                            blast_level=jev.blast_radius.level,
                            blast_score=jev.blast_radius.score,
                            human_review_probability=jev.human_review.probability,
                            breaking_risk=True,
                            graph_context=graph_context,
                        )
                        items.append(AttentionItem(
                            title=f"Breaking Change: {title_symbol}",
                            level="HIGH",
                            detail=raw_detail,
                            action_required="Audit all callers/importers of this symbol before merging.",
                            jev_attention_probability=jev.breaking_risk.probability,
                            llm_summary=deep_analysis.get("summary"),
                            symbol=symbol or file,
                            file_path=file,
                            hunk_id=hunk.id,
                            diff_snippet=diff_snippet,
                            graph_context=graph_context,
                            llm_analysis=deep_analysis,
                        ))

                # Rule 3 — Human review required by Jev
                if jev.human_review.required and jev.human_review.probability >= REVIEW_THRESHOLD:
                    key = ("rule3", symbol, file)
                    if key not in emitted:
                        emitted.add(key)
                        raw_detail = (
                            f"Jev human review probability {jev.human_review.probability:.0%} "
                            f"exceeds {REVIEW_THRESHOLD:.0%} threshold for {display_symbol}."
                        )
                        deep_analysis = gemini_client.synthesize_deep_risk_analysis(
                            rule="Senior Review Required",
                            symbol=symbol or file,
                            file_path=file,
                            diff_snippet=diff_snippet,
                            jev_category=jev.classification.category,
                            blast_level=jev.blast_radius.level,
                            blast_score=jev.blast_radius.score,
                            human_review_probability=jev.human_review.probability,
                            breaking_risk=jev.breaking_risk.is_breaking,
                            graph_context=graph_context,
                        )
                        items.append(AttentionItem(
                            title=f"Senior Review Required: {title_symbol}",
                            level="MEDIUM",
                            detail=raw_detail,
                            action_required="This hunk requires senior developer sign-off per Jev analysis.",
                            jev_attention_probability=jev.human_review.probability,
                            llm_summary=deep_analysis.get("summary"),
                            symbol=symbol or file,
                            file_path=file,
                            hunk_id=hunk.id,
                            diff_snippet=diff_snippet,
                            graph_context=graph_context,
                            llm_analysis=deep_analysis,
                        ))

                # Rule 5 — Security category hunk
                if jev.classification.category == "SECURITY_RELEVANT_CHANGE":
                    key = ("rule5", symbol, file)
                    if key not in emitted:
                        emitted.add(key)
                        raw_detail = f"Jev classified {display_symbol} as a security-relevant change."
                        deep_analysis = gemini_client.synthesize_deep_risk_analysis(
                            rule="Security Review",
                            symbol=symbol or file,
                            file_path=file,
                            diff_snippet=diff_snippet,
                            jev_category=jev.classification.category,
                            blast_level=jev.blast_radius.level,
                            blast_score=jev.blast_radius.score,
                            human_review_probability=jev.human_review.probability,
                            breaking_risk=jev.breaking_risk.is_breaking,
                            graph_context=graph_context,
                        )
                        items.append(AttentionItem(
                            title=f"Security-Sensitive Change: {title_symbol}",
                            level="HIGH",
                            detail=raw_detail,
                            action_required=(
                                "Perform security review — authentication, authorization, "
                                "or validation logic modified."
                            ),
                            jev_attention_probability=0.95,
                            llm_summary=deep_analysis.get("summary"),
                            symbol=symbol or file,
                            file_path=file,
                            hunk_id=hunk.id,
                            diff_snippet=diff_snippet,
                            graph_context=graph_context,
                            llm_analysis=deep_analysis,
                        ))

                # Rule 6 — API contract change
                if jev.classification.category == "API_CHANGE":
                    key = ("rule6", symbol, file)
                    if key not in emitted:
                        emitted.add(key)
                        raw_detail = f"Jev classified {display_symbol} as an API contract change."
                        deep_analysis = gemini_client.synthesize_deep_risk_analysis(
                            rule="API Compatibility Review",
                            symbol=symbol or file,
                            file_path=file,
                            diff_snippet=diff_snippet,
                            jev_category=jev.classification.category,
                            blast_level=jev.blast_radius.level,
                            blast_score=jev.blast_radius.score,
                            human_review_probability=jev.human_review.probability,
                            breaking_risk=jev.breaking_risk.is_breaking,
                            graph_context=graph_context,
                        )
                        items.append(AttentionItem(
                            title=f"Public API Contract Changed: {title_symbol}",
                            level="MEDIUM",
                            detail=raw_detail,
                            action_required="Check all consumers of this API for compatibility.",
                            jev_attention_probability=0.75,
                            llm_summary=deep_analysis.get("summary"),
                            symbol=symbol or file,
                            file_path=file,
                            hunk_id=hunk.id,
                            diff_snippet=diff_snippet,
                            graph_context=graph_context,
                            llm_analysis=deep_analysis,
                        ))

                # Rule 8 — Unattended callers / Omission risk (Serena + Jev)
                if jev.omission and jev.omission.has_omission_risk:
                    key = ("rule8", symbol, file)
                    if key not in emitted:
                        emitted.add(key)
                        sev_level = "HIGH" if jev.omission.severity in ("critical_breaking", "medium_behavioral") else "MEDIUM"
                        raw_detail = jev.omission.rationale or f"Jev and Serena detected unattended callers for {display_symbol}."
                        deep_analysis = gemini_client.synthesize_deep_risk_analysis(
                            rule="Omission / Unattended Caller Review",
                            symbol=symbol or file,
                            file_path=file,
                            diff_snippet=diff_snippet,
                            jev_category=jev.classification.category,
                            blast_level=jev.blast_radius.level,
                            blast_score=jev.blast_radius.score,
                            human_review_probability=jev.human_review.probability,
                            breaking_risk=jev.breaking_risk.is_breaking,
                            omission_rationale=jev.omission.rationale or "",
                            graph_context=graph_context,
                        )
                        items.append(AttentionItem(
                            title=f"Unattended Caller Risk: {title_symbol}",
                            level=sev_level,
                            detail=raw_detail,
                            action_required=jev.omission.action_recommendation or "Audit untouched callers for broken assumptions.",
                            jev_attention_probability=jev.omission.probability or 0.85,
                            llm_summary=deep_analysis.get("summary"),
                            symbol=symbol or file,
                            file_path=file,
                            hunk_id=hunk.id,
                            diff_snippet=diff_snippet,
                            graph_context=graph_context,
                            llm_analysis=deep_analysis,
                        ))

                # Rule 9 — Stale code removal suggestion (commonization, deleted, deprecated, or dead code)
                is_stale_candidate = (
                    jev.classification.category == "COMMONIZATION"
                    or hunk.change_type == "DELETED"
                    or any(
                        kw in (hunk.old_lines + " " + hunk.new_lines + " " + symbol + " " + file).lower()
                        for kw in (
                            "@deprecated",
                            "deprecated",
                            "stale",
                            "dead_code",
                            "obsolete",
                            "unused",
                            "legacy_",
                            "todo: remove",
                            "todo: clean",
                        )
                    )
                )
                if is_stale_candidate:
                    key = ("rule9", symbol, file)
                    if key not in emitted:
                        emitted.add(key)
                        raw_detail = (
                            f"Stale or superseded code detected in {display_symbol}. "
                            "Verify that obsolete implementations, dead helpers, and duplicate branches are cleanly removed."
                        )
                        deep_analysis = gemini_client.synthesize_deep_risk_analysis(
                            rule="Stale Code Removal",
                            symbol=symbol or file,
                            file_path=file,
                            diff_snippet=diff_snippet,
                            jev_category=jev.classification.category,
                            blast_level=jev.blast_radius.level,
                            blast_score=jev.blast_radius.score,
                            human_review_probability=jev.human_review.probability,
                            breaking_risk=jev.breaking_risk.is_breaking,
                            graph_context=graph_context,
                        )
                        items.append(AttentionItem(
                            title=f"Stale Code Removal: {title_symbol}",
                            level="LOW",
                            detail=raw_detail,
                            action_required="Audit and remove stale, deprecated, or superseded code to prevent technical debt.",
                            jev_attention_probability=0.40,
                            llm_summary=deep_analysis.get("summary"),
                            symbol=symbol or file,
                            file_path=file,
                            hunk_id=hunk.id,
                            diff_snippet=diff_snippet,
                            graph_context=graph_context,
                            llm_analysis=deep_analysis,
                        ))

            # Rule 4 — Cross-service coupling (3+ affected services)
            if len(change.affected_services) >= 3:
                key = ("rule4", "", "")
                if key not in emitted:
                    emitted.add(key)
                    services_list = ", ".join(change.affected_services)
                    deep_analysis = gemini_client.synthesize_deep_risk_analysis(
                        rule="Cross-Service Coupling Review",
                        symbol="Cross-service boundary",
                        file_path="Multiple services",
                        diff_snippet="; ".join(h.symbol for h in change.hunks if h.symbol),
                        jev_category=change.category,
                        blast_level="system_critical" if len(change.affected_services) >= 4 else "service_level",
                        blast_score=4.0,
                        human_review_probability=0.82,
                        breaking_risk=False,
                        graph_context={"coupled_services": change.affected_services},
                    )
                    items.append(AttentionItem(
                        title="Cross-Service Coupling Detected",
                        level="MEDIUM",
                        detail=(
                            f"{len(change.affected_services)} services are coupled by this change: "
                            f"{services_list}."
                        ),
                        action_required=(
                            f"Run integration tests across all affected services "
                            f"({services_list}) before merging."
                        ),
                        jev_attention_probability=0.82,
                        llm_summary=deep_analysis.get("summary"),
                        symbol="Cross-service architecture",
                        file_path=services_list,
                        graph_context={"coupled_services": change.affected_services},
                        llm_analysis=deep_analysis,
                    ))

            # Rule 7 — Legacy keyword fallback (LOW, only if no Jev-driven items emitted)
            if len(items) == jev_item_count_before:
                services_str = " ".join(change.affected_services).lower()
                change_context_str = (services_str + " " + change.title.lower() + " " + change.why_explanation.lower())
                if any(kw in services_str for kw in ("payment", "charge", "refund")):
                    deep_analysis = gemini_client.synthesize_deep_risk_analysis(
                        rule="Payment Idempotency Review",
                        symbol="Payment transaction",
                        file_path=", ".join(change.affected_services),
                        diff_snippet="Payment mutation path",
                        jev_category="REFACTOR",
                        blast_level="service_level",
                        blast_score=2.5,
                        human_review_probability=0.4,
                        breaking_risk=False,
                        graph_context={"coupled_services": change.affected_services},
                    )
                    items.append(AttentionItem(
                        title="Payment Logic Modified — verify idempotency.",
                        level="LOW",
                        detail=f"Keyword signal detected in affected services: {', '.join(change.affected_services)}.",
                        action_required="Ensure payment mutations use idempotency keys.",
                        jev_attention_probability=0.4,
                        llm_summary=deep_analysis.get("summary"),
                        symbol="Payment Service",
                        file_path=", ".join(change.affected_services),
                        graph_context={"coupled_services": change.affected_services},
                        llm_analysis=deep_analysis,
                    ))
                elif any(kw in services_str for kw in ("auth", "token", "interceptor")):
                    deep_analysis = gemini_client.synthesize_deep_risk_analysis(
                        rule="Auth Token Lifecycle Review",
                        symbol="Auth token handler",
                        file_path=", ".join(change.affected_services),
                        diff_snippet="Authentication handler path",
                        jev_category="REFACTOR",
                        blast_level="service_level",
                        blast_score=2.5,
                        human_review_probability=0.4,
                        breaking_risk=False,
                        graph_context={"coupled_services": change.affected_services},
                    )
                    items.append(AttentionItem(
                        title="Auth Logic Modified — verify token lifecycle.",
                        level="LOW",
                        detail=f"Keyword signal detected in affected services: {', '.join(change.affected_services)}.",
                        action_required="Verify token refresh logic does not cause recursive loops.",
                        jev_attention_probability=0.4,
                        llm_summary=deep_analysis.get("summary"),
                        symbol="Auth Service",
                        file_path=", ".join(change.affected_services),
                        graph_context={"coupled_services": change.affected_services},
                        llm_analysis=deep_analysis,
                    ))
                elif any(kw in change_context_str for kw in ("stale", "cleanup", "removal", "deprecated", "unused", "dead", "obsolete", "prune")):
                    deep_analysis = gemini_client.synthesize_deep_risk_analysis(
                        rule="Stale Code Removal",
                        symbol="Stale or deprecated logic",
                        file_path=", ".join(change.affected_services) if change.affected_services else "Cleaned files",
                        diff_snippet="Stale code removal path",
                        jev_category="REFACTOR",
                        blast_level="localized",
                        blast_score=1.5,
                        human_review_probability=0.35,
                        breaking_risk=False,
                        graph_context={"coupled_services": change.affected_services},
                    )
                    items.append(AttentionItem(
                        title="Stale Code Removal: Verify deprecated references are pruned.",
                        level="LOW",
                        detail=f"Keyword signal detected for stale code in: {', '.join(change.affected_services) if change.affected_services else change.title}.",
                        action_required="Audit repository for lingering callers and prune obsolete code paths or dead implementations.",
                        jev_attention_probability=0.35,
                        llm_summary=deep_analysis.get("summary"),
                        symbol="Stale Code Reference",
                        file_path=", ".join(change.affected_services) if change.affected_services else "Cleaned files",
                        graph_context={"coupled_services": change.affected_services},
                        llm_analysis=deep_analysis,
                    ))

        return items

risk_engine = RiskEngine()
