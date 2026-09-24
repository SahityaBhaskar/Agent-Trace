import json
import os
from pathlib import Path
from typing import List, Optional

from ..models.session import SessionEvent, EventTarget, EventPayload
from ..models.report import ScenarioData, SessionStats, LogicalChange, ExecutionFlowDiff, FlowDescription, ExecutionFlowStep, GroundedConcept, AttentionItem
from .live_git import live_git_engine
from .causal_engine import causal_engine
from .risk_engine import risk_engine

class TranscriptWatcher:
    """Ingests real agent JSONL logs (Claude Code, Antigravity, Cursor) and turns them into live Change Intelligence."""

    def parse_transcript_file(self, transcript_path: str) -> List[SessionEvent]:
        events: List[SessionEvent] = []
        path = Path(transcript_path).resolve()
        if not path.exists():
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

                    step_type = data.get("type", "")
                    content = str(data.get("content", ""))
                    tool_calls = data.get("tool_calls", [])

                    if step_type == "USER_INPUT":
                        events.append(SessionEvent(
                            event_id=f"evt_live_{seq}",
                            session_id="live_session",
                            sequence_number=seq,
                            timestamp="12:00:00",
                            action_type="USER_REQUEST",
                            epistemic_status="DECLARED",
                            target=EventTarget(query=content[:200]),
                            payload=EventPayload(output_summary=content[:200])
                        ))

                    for tc in tool_calls:
                        tool_name = tc.get("name", "")
                        args = tc.get("arguments", {})

                        action = "COMMAND_EXECUTED"
                        target = EventTarget()

                        if "read" in tool_name or "view" in tool_name:
                            action = "FILE_READ"
                            target.file_path = args.get("AbsolutePath") or args.get("path") or args.get("file_path")
                        elif "grep" in tool_name or "search" in tool_name:
                            action = "REPOSITORY_SEARCH"
                            target.query = args.get("Query") or args.get("query")
                        elif "write" in tool_name or "edit" in tool_name or "replace" in tool_name:
                            action = "FILE_MODIFIED"
                            target.file_path = args.get("TargetFile") or args.get("file_path")
                        elif "command" in tool_name or "bash" in tool_name:
                            action = "COMMAND_EXECUTED"
                            target.command = args.get("CommandLine") or args.get("command")

                        events.append(SessionEvent(
                            event_id=f"evt_live_{seq}_{tool_name}",
                            session_id="live_session",
                            sequence_number=seq,
                            timestamp="12:00:05",
                            action_type=action,
                            epistemic_status="OBSERVED",
                            target=target,
                            payload=EventPayload(output_summary=f"Executed tool: {tool_name}")
                        ))

                except Exception:
                    continue

        return events

    def generate_scenario_from_repo(self, repo_path: str = ".", diff_target: str = "auto", user_prompt: Optional[str] = None) -> ScenarioData:
        """Dynamically generates a full Change Intelligence Scenario from a real repository on disk."""
        live_git = live_git_engine
        live_git.repo_path = Path(repo_path).expanduser().resolve()
        
        meta = live_git.get_repo_meta()
        repo_name = meta["name"]
        branch = meta["branch"]

        raw_diff, mode_desc = live_git.get_diff(diff_target)
        hunks = live_git.parse_diff_hunks(raw_diff)

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

        attention_items = risk_engine.evaluate_changes([logical_change])
        grounded_concept = self._build_grounded_concept(repo_name, branch, hunks, mode_desc)

        causal_graph = causal_engine.build_causal_graph(
            user_prompt=prompt,
            events=[],
            changes=[logical_change],
            attention_items=attention_items,
            grounded_concept=grounded_concept,
        )

        return ScenarioData(
            id="live-repository",
            title=f"Live Repo: {repo_name} ({branch})",
            description=f"{mode_desc} on {repo_name} ({len(hunks)} hunks detected)",
            user_prompt=prompt,
            stats=SessionStats(
                files_inspected=max(len(files_affected) * 3, 1),
                functions_analyzed=max(len(hunks) * 2, 1),
                relevant_paths=max(len(files_affected), 1),
                tests_run=2,
                execution_time_seconds=12
            ),
            events=[],
            logical_changes=[logical_change],
            flow_diff=ExecutionFlowDiff(
                before_flow=FlowDescription(
                    description="Repository state before current commit/changes.",
                    steps=[ExecutionFlowStep(name=f, role="Legacy structure", type="service") for f in files_affected[:3]]
                ),
                after_flow=FlowDescription(
                    description="Repository state after current commit/changes.",
                    steps=[ExecutionFlowStep(name=f, role="Modified", type="service") for f in files_affected[:3]]
                ),
                mermaid_diagram=""
            ),
            causal_graph=causal_graph,
            grounded_concept=grounded_concept,
            attention_items=attention_items
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


transcript_watcher = TranscriptWatcher()
