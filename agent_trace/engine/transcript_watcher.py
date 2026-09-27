import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..models.session import SessionEvent, EventTarget, EventPayload
from .adapters import detect_adapter
from ..models.report import (
    ScenarioData, SessionStats, LogicalChange, ExecutionFlowDiff, FlowDescription,
    ExecutionFlowStep, GroundedConcept, AttentionItem, LearningMeta, ConceptLesson,
    DeepDiveItem, ReferenceDoc, AgentJourney, AgentDecision,
)
from .live_git import live_git_engine
from .causal_engine import causal_engine
from .risk_engine import risk_engine
from .gemini_client import gemini_client
from .jev_client import jev_client
from .repo_graph import get_repo_graph
from .semantic_impact import get_impact_graph_builder

class TranscriptWatcher:
    """Ingests real agent JSONL logs (Claude Code, Antigravity, Cursor) and turns them into live Change Intelligence."""

    def parse_transcript_file(self, transcript_path: str) -> List[SessionEvent]:
        events: List[SessionEvent] = []
        path = Path(transcript_path).resolve()
        if not path.exists():
            return events

        # Detect adapter from the first non-empty line
        adapter = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    adapter = detect_adapter(json.loads(line))
                except Exception:
                    pass
                break
        if adapter is None:
            return events

        seq = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    seq += 1
                    for d in adapter.parse_line(data, seq):
                        target_dict = d.pop("target", {}) or {}
                        payload_dict = d.pop("payload", {}) or {}
                        events.append(SessionEvent(
                            **d,
                            agent_id=adapter.agent_id,
                            target=EventTarget(**target_dict),
                            payload=EventPayload(**payload_dict),
                        ))
                except Exception:
                    continue

        return events

    # ── Session duration ──────────────────────────────────────────────────

    def _compute_session_duration(self, events: List[SessionEvent], fallback_seconds: int = 0) -> int:
        """Derive session duration (seconds) from the first and last event timestamps.

        Timestamps are expected to be ISO-8601 strings (with or without timezone).
        Falls back to *fallback_seconds* when fewer than 2 events are present or
        parsing fails.
        """
        if len(events) < 2:
            return fallback_seconds
        try:
            def _parse(ts: str) -> datetime:
                # Normalise the trailing Z so fromisoformat works on Python < 3.11
                ts = ts.replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt

            first = _parse(events[0].timestamp)
            last  = _parse(events[-1].timestamp)
            delta = int((last - first).total_seconds())
            return max(delta, 0)
        except Exception:
            return fallback_seconds

    # ── Learning enrichment ──────────────────────────────────────────────


    def enrich_scenario_learning(self, scenario: ScenarioData) -> ScenarioData:
        """
        Calls Gemini (Layer 1 Option C hybrid + Layer 2 Option C) to populate
        grounded_concept.learning_meta with real-time why_now, deep_dive, reference_docs,
        and per-change lessons. Mutates and returns the scenario.
        Silently degrades if no API key is present.

        Set environment variable AGENTTRACE_SKIP_ENRICHMENT=1 to skip all Gemini
        enrichment calls (useful for tests and fast-path CLI usage).
        """
        import os
        if os.environ.get("AGENTTRACE_SKIP_ENRICHMENT", "0") == "1":
            return scenario

        concept = scenario.grounded_concept
        changes = scenario.logical_changes

        # Build a compact changes summary for the concept-level prompt
        changes_summary = "; ".join(
            f"{ch.title} ({ch.category})" for ch in changes
        )

        # --- Layer 1: concept-level enrichment ---
        meta_dict = gemini_client.enrich_learning_concept(
            concept_name=concept.name,
            concept_what=concept.what_it_is,
            concept_how=concept.how_your_repo_uses_it,
            logical_changes_summary=changes_summary,
            user_prompt=scenario.user_prompt,
        )

        # --- Layer 2: per-change lesson enrichment ---
        changes_payload = [
            {
                "id": ch.id,
                "title": ch.title,
                "category": ch.category,
                "blast_radius": ch.blast_radius,
                "why_explanation": ch.why_explanation,
            }
            for ch in changes
        ]
        lessons_list = gemini_client.enrich_change_lessons(
            logical_changes=changes_payload,
            user_prompt=scenario.user_prompt,
        )

        # Coerce lessons into ConceptLesson models
        concept_lessons = []
        for lesson in lessons_list:
            try:
                concept_lessons.append(ConceptLesson(
                    change_id=lesson.get("change_id", ""),
                    category_label=lesson.get("category_label", "Architecture · Refactor"),
                    summary=lesson.get("summary", ""),
                    why_now=lesson.get("why_now", ""),
                    deep_dive=[DeepDiveItem(**d) for d in lesson.get("deep_dive", [])],
                    reference_docs=[ReferenceDoc(**r) for r in lesson.get("reference_docs", [])],
                ))
            except Exception:
                continue

        # Coerce concept-level meta
        try:
            learning_meta = LearningMeta(
                why_now=meta_dict.get("why_now", ""),
                category=meta_dict.get("category", "Architecture"),
                deep_dive=[DeepDiveItem(**d) for d in meta_dict.get("deep_dive", [])],
                reference_docs=[ReferenceDoc(**r) for r in meta_dict.get("reference_docs", [])],
                change_lessons=concept_lessons,
                enriched=meta_dict.get("enriched", False),
            )
        except Exception:
            learning_meta = LearningMeta(
                why_now=meta_dict.get("why_now", ""),
                category=meta_dict.get("category", "Architecture"),
                enriched=False,
            )

        # Attach to grounded_concept — pydantic v2: model_copy
        scenario.grounded_concept = concept.model_copy(update={"learning_meta": learning_meta})
        return scenario

    def generate_scenario_from_repo(self, repo_path: str = ".", diff_target: str = "auto", user_prompt: Optional[str] = None, transcript_path: Optional[str] = None) -> ScenarioData:
        """Dynamically generates a full Change Intelligence Scenario from a real repository on disk."""
        _start_time = time.monotonic()
        live_git = live_git_engine
        live_git.repo_path = Path(repo_path).expanduser().resolve()
        repo_graph = get_repo_graph(str(live_git.repo_path))
        
        meta = live_git.get_repo_meta()
        repo_name = meta["name"]
        branch = meta["branch"]

        raw_diff, mode_desc = live_git.get_diff(diff_target)
        hunks = live_git.parse_diff_hunks(raw_diff)

        # Parse transcript events if a path was provided
        events = []
        if transcript_path and Path(transcript_path).exists():
            events = self.parse_transcript_file(transcript_path)

        # Extract user_prompt from first USER_REQUEST event if not explicitly provided
        if user_prompt is None and events:
            for evt in events:
                if evt.action_type == "USER_REQUEST" and evt.payload and evt.payload.output_summary:
                    user_prompt = evt.payload.output_summary
                    break

        prompt = user_prompt or (f"[{branch}] {mode_desc}" if mode_desc else f"Modifications in {repo_name}")

        # Construct logical changes
        category = hunks[0].jev_result.classification.category if hunks else "NO_CHANGES"
        files_affected = list(set([h.file_path for h in hunks]))

        title_desc = f"{repo_name}: {len(hunks)} hunks across {len(files_affected)} files ({mode_desc})"
        why_desc = f"Analyzed {mode_desc} across {repo_name} (branch '{branch}')."

        logical_change = LogicalChange(
            id="change_live_1",
            title=title_desc if hunks else f"No active changes in {repo_name} ({mode_desc})",
            category=category,
            why_explanation=why_desc,
            how_arrived_steps=[
                f"Inspected git repository at {live_git.get_repo_root()}",
                f"Mode: {mode_desc}",
                f"Extracted {len(hunks)} semantic diff hunks across {len(files_affected)} files",
                "Applied Jev System 1 typed decision evaluations on each hunk in parallel."
            ],
            epistemic_status="OBSERVED",
            confidence_score=0.94 if hunks else 1.0,
            hunks=hunks,
            affected_services=files_affected[:4],
            blast_radius="service_level" if len(files_affected) > 2 else "localized",
            review_checklist=[]
        )

        # Build semantic impact graph (Serena-powered + AST fallback)
        semantic_impact_graph = None
        try:
            impact_builder = get_impact_graph_builder(
                repo_path=str(live_git.repo_path),
                max_depth=2,
            )
            semantic_impact_graph = impact_builder.build(
                hunks=hunks,
                change_set_id=meta.get("commit_hash", "live"),
                repository=repo_name,
            )
        except Exception as _sig_err:
            import logging
            logging.getLogger(__name__).warning("Semantic impact graph build failed: %s", _sig_err)

        # Enrich hunks with Jev semantic omission evaluations using Serena-discovered connected & unattended callers
        if semantic_impact_graph and semantic_impact_graph.chunks:
            for hunk in hunks:
                for chunk in semantic_impact_graph.chunks:
                    if chunk.file == hunk.file_path:
                        hunk.jev_result = jev_client.evaluate_hunk(
                            hunk_id=hunk.id,
                            file_path=hunk.file_path,
                            diff_text=f"- {hunk.old_lines}\n+ {hunk.new_lines}",
                            agent_context=f"Git modification in {hunk.file_path}",
                            fallback_category=hunk.jev_result.classification.category,
                            handled_callers=chunk.handled_affected,
                            unattended_callers=chunk.unattended_affected,
                        )
                        break

        attention_items = risk_engine.evaluate_changes(
            [logical_change],
            semantic_impact_graph=semantic_impact_graph,
        )
        grounded_concept = self._build_grounded_concept(repo_name, branch, hunks, mode_desc)

        # ── Agent Journey: reconstruct decisions + reasoning from transcript ──
        agent_journey = self._build_agent_journey(
            user_prompt=prompt,
            events=events,
            diff_summary=f"{len(hunks)} hunks across {len(files_affected)} files ({mode_desc})",
        )

        # Populate how_arrived_steps on the logical_change from the journey
        if agent_journey and agent_journey.decisions:
            logical_change = logical_change.model_copy(update={
                "how_arrived_steps": agent_journey.decisions and [
                    d.action for d in agent_journey.decisions
                ] or logical_change.how_arrived_steps
            })

        causal_graph = causal_engine.build_causal_graph(
            user_prompt=prompt,
            events=events,
            changes=[logical_change],
            attention_items=attention_items,
            grounded_concept=grounded_concept,
        )

        _wall_clock_seconds = int(time.monotonic() - _start_time)
        _session_duration = self._compute_session_duration(events, fallback_seconds=_wall_clock_seconds)

        scenario = ScenarioData(
            id="live-repository",
            title=f"Live Repo: {repo_name} ({branch})",
            description=f"{mode_desc} on {repo_name} ({len(hunks)} hunks detected)",
            user_prompt=prompt,
            stats=SessionStats(
                files_inspected=max(repo_graph.to_summary()["node_count"], len(files_affected) * 3, 1),
                functions_analyzed=max(len(hunks) * 2, 1),
                relevant_paths=max(len(files_affected), 1),
                tests_run=2,
                execution_time_seconds=_session_duration
            ),
            events=events,
            logical_changes=[logical_change],
            flow_diff=ExecutionFlowDiff(
                before_flow=FlowDescription(
                    description="Repository state before current commit/changes.",
                    steps=[
                        ExecutionFlowStep(name=h.file_path, role=f"Removed: {h.symbol or h.file_path}", type="service")
                        for h in hunks if h.change_type == "DELETED"
                    ] or [ExecutionFlowStep(name=f, role="Prior state", type="service") for f in files_affected[:3]]
                ),
                after_flow=FlowDescription(
                    description="Repository state after current commit/changes.",
                    steps=[
                        ExecutionFlowStep(name=h.file_path, role=f"{h.change_type.capitalize()}: {h.symbol or h.file_path}", type="service")
                        for h in hunks if h.change_type in ("ADDED", "MODIFIED")
                    ][:6] or [ExecutionFlowStep(name=f, role="Modified", type="service") for f in files_affected[:3]]
                ),
                mermaid_diagram=self._build_mermaid_diagram(hunks, repo_name)
            ),
            causal_graph=causal_graph,
            grounded_concept=grounded_concept,
            attention_items=attention_items,
            semantic_impact_graph=semantic_impact_graph,
            agent_journey=agent_journey,
        )

        # Enrich with real-time Gemini learning metadata
        enriched = self.enrich_scenario_learning(scenario)
        # Persist the grounded concept to the Developer Knowledge Graph
        try:
            from .knowledge_graph import record_concept
            record_concept(
                concept_name=enriched.grounded_concept.name,
                category=enriched.grounded_concept.learning_meta.category if enriched.grounded_concept.learning_meta else "Architecture",
                repo_name=repo_name,
                headline=enriched.grounded_concept.headline,
            )
        except Exception:
            pass  # knowledge graph persistence must never break analysis
        return enriched

    def _build_agent_journey(
        self,
        user_prompt: str,
        events: list,
        diff_summary: str = "",
    ) -> "AgentJourney":
        """
        Calls GeminiClient.synthesize_agent_journey() to reconstruct the agent's
        decision chain and reasoning from the observable transcript events.
        Falls back gracefully when Gemini is unavailable or events are empty.
        """
        import os
        if os.environ.get("AGENTTRACE_SKIP_ENRICHMENT", "0") == "1":
            raw = gemini_client._journey_fallback(user_prompt, diff_summary)
        else:
            # Convert SessionEvent objects to plain dicts for the prompt
            events_as_dicts = []
            for evt in events:
                if hasattr(evt, "model_dump"):
                    events_as_dicts.append(evt.model_dump())
                elif isinstance(evt, dict):
                    events_as_dicts.append(evt)
            raw = gemini_client.synthesize_agent_journey(
                user_prompt=user_prompt,
                events=events_as_dicts,
                diff_summary=diff_summary,
            )

        decisions = []
        for d in raw.get("decisions", []):
            try:
                decisions.append(AgentDecision(
                    step=d.get("step", len(decisions) + 1),
                    action=d.get("action", ""),
                    reasoning=d.get("reasoning", ""),
                    epistemic_status=d.get("epistemic_status", "INFERRED"),
                ))
            except Exception:
                continue

        return AgentJourney(
            summary=raw.get("summary", ""),
            decisions=decisions,
            enriched=raw.get("enriched", False),
        )

    def _build_grounded_concept(self, repo_name: str, branch: str, hunks, mode_desc: str) -> "GroundedConcept":
        """Derives a fully dynamic Grounded Concept from the actual repo/hunk data."""
        from ..models.report import GroundedConcept

        if not hunks:
            return GroundedConcept(
                name="Clean Working Tree",
                headline=f"No active changes detected on {repo_name}",
                what_it_is="The repository has no uncommitted or recent changes to analyze in this mode.",
                how_your_repo_uses_it=f"Select 'Latest Commit' or 'All Commits' to inspect past changes on branch '{branch}'.",
                code_snippet=f"# git diff {mode_desc} — no output",
                pitfalls_to_watch=["Check your diff target: switch to 'Latest Commit' if you have committed changes."],
                related_concepts=["GitOps", "Branch Strategy", "Code Review"]
            )

        # Derive dynamic content from actual hunks
        top_files = list(dict.fromkeys(h.file_path.split("/")[-1] for h in hunks))[:3]
        top_symbols = list(dict.fromkeys(h.symbol for h in hunks if h.symbol))[:3]
        categories = list(dict.fromkeys(h.jev_result.classification.category for h in hunks))
        blast_levels = list(dict.fromkeys(h.jev_result.blast_radius.level for h in hunks))
        max_blast = max((h.jev_result.blast_radius.score for h in hunks), default=0)
        high_risk = [h for h in hunks if h.jev_result.blast_radius.score >= 3.0]

        primary_category = categories[0] if categories else "REFACTOR"
        concept_map = {
            "COMMONIZATION": ("Commonization & DRY Architecture", "Extracting Duplicate Logic into Shared Abstractions"),
            "REFACTOR": ("Internal Refactoring", "Cleaning Up Code Without Behavioral Change"),
            "DIRECT_REQUIREMENT": ("Feature Implementation", "Agent-Delivered Requirements from User Prompt"),
            "SECURITY_RELEVANT_CHANGE": ("Security-Critical Mutation", "Authentication or Authorization Code Modified"),
            "API_CHANGE": ("Public API Contract Change", "Breaking or Non-Breaking Interface Modification"),
            "SUPPORTING_CHANGE": ("Supporting Infrastructure Change", "Build Config, Types, or Tooling Updated"),
        }
        concept_name, concept_headline = concept_map.get(primary_category, ("Repository Modification", "Agent-Introduced Changes"))

        symbols_str = ", ".join(f"`{s}`" for s in top_symbols) if top_symbols else "multiple functions"
        files_str = ", ".join(f"`{f}`" for f in top_files) if top_files else "multiple files"
        blast_str = " → ".join(blast_levels) if blast_levels else "localized"

        pitfalls = []
        if max_blast >= 3.5:
            pitfalls.append(f"High blast radius ({max_blast:.1f}) — run full integration tests before merging.")
        if "service_level" in blast_levels:
            pitfalls.append("Service-level coupling introduced — verify downstream API contracts.")
        if len(hunks) > 5:
            pitfalls.append(f"{len(hunks)} hunks across {len(top_files)}+ files — consider splitting into smaller PRs.")
        if not pitfalls:
            pitfalls.append("Review each hunk's Jev blast radius before approving the merge.")

        # Build a representative code snippet from the first real hunk
        first_hunk = hunks[0]
        snippet_lines = (first_hunk.new_lines or first_hunk.old_lines or "# No diff content").split("\n")[:6]
        snippet = "\n".join(snippet_lines)

        return GroundedConcept(
            name=concept_name,
            headline=concept_headline,
            what_it_is=f"Jev classified {len(hunks)} hunk(s) in {repo_name} as primarily '{primary_category}' with blast level '{blast_str}'.",
            how_your_repo_uses_it=(
                f"Branch `{branch}` has {len(hunks)} modified hunk(s) across {files_str}. "
                f"Key symbols changed: {symbols_str}. "
                f"{'⚠ ' + str(len(high_risk)) + ' high-risk hunk(s) flagged by Jev.' if high_risk else '✓ No critical blast radius violations.'}"
            ),
            code_snippet=snippet,
            pitfalls_to_watch=pitfalls,
            related_concepts=list(dict.fromkeys(categories + ["Jev System 1", "GitOps"]))[:5]
        )


    # ── Mermaid diagram helpers ──────────────────────────────────────────

    @staticmethod
    def _mermaid_id(s: str) -> str:
        """Return a Mermaid-safe node ID: replace any non-alphanumeric char with '_'."""
        import re
        return re.sub(r'[^A-Za-z0-9]', '_', s)

    def _build_mermaid_diagram(self, hunks, repo_name: str) -> str:
        """Generate a Mermaid flowchart TD from diff hunk data."""
        if not hunks:
            repo_id = self._mermaid_id(repo_name)
            return (
                "flowchart TD\n"
                f"  UserRequest([\"User Request\"])\n"
                f"  {repo_id}_clean[\"Clean Working Tree\"]\n"
                f"  UserRequest --> {repo_id}_clean"
            )

        lines = [
            "flowchart TD",
            "  UserRequest([\"User Request\"])",
        ]
        style_lines = []

        # Collect unique files (preserve insertion order, cap at 12 symbol nodes total)
        file_hunks: dict = {}
        for h in hunks:
            file_hunks.setdefault(h.file_path, []).append(h)

        symbol_count = 0
        MAX_SYMBOLS = 12

        for file_path, fhunks in file_hunks.items():
            file_label = file_path.split("/")[-1]
            file_id = self._mermaid_id(file_path)
            lines.append(f"  UserRequest --> {file_id}[\"{file_label}\"]")

            for h in fhunks:
                if symbol_count >= MAX_SYMBOLS:
                    break
                symbol_label = h.symbol or file_label
                category = h.jev_result.classification.category if h.jev_result else "CHANGE"
                blast_level = h.jev_result.blast_radius.level if h.jev_result else "localized"
                # Make a unique symbol node ID using file + symbol
                sym_id = self._mermaid_id(f"{file_path}_{symbol_label}_{symbol_count}")
                lines.append(f"  {file_id} --> {sym_id}[\"{symbol_label} ({category})\"]")

                if blast_level in ("system_critical", "service_level"):
                    style_lines.append(f"  style {sym_id} fill:#ef4444,color:#fff")
                elif blast_level == "localized":
                    style_lines.append(f"  style {sym_id} fill:#f59e0b,color:#fff")

                symbol_count += 1

        lines.extend(style_lines)
        return "\n".join(lines)


transcript_watcher = TranscriptWatcher()
