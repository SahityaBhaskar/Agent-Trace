import os
import sys
import argparse
from pathlib import Path
from typing import Optional

from .engine.jev_client import jev_client
from .engine.transcript_watcher import transcript_watcher
from .scenarios.payment_retry import get_payment_retry_scenario
from .scenarios.auth_interceptor import get_auth_scenario
from .server import run_server

# ANSI Color Codes for terminal
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"

def print_banner():
    print(f"\n{BOLD}{CYAN}========================================================================{RESET}")
    print(f"{BOLD}{CYAN}  AGENTTRACE — DEVELOPER CONTROL PLANE FOR AI CODING AGENTS{RESET}")
    print(f"{DIM}  Dual-Brain Engine: System 1 (Jev TypeSafe AI) + System 2 (Gemini){RESET}")
    print(f"{BOLD}{CYAN}========================================================================{RESET}\n")

def print_scenario_report(scenario_data):
    scenario = scenario_data
    print_banner()

    print(f"{BOLD}REPOSITORY / CONTEXT:{RESET} {BOLD}{CYAN}{scenario.title}{RESET}")
    print(f"{BOLD}TRIGGER / PROMPT:{RESET} \"{scenario.user_prompt}\"")
    print(f"{DIM}Session Duration: {scenario.stats.execution_time_seconds}s | Execution Paths: {scenario.stats.relevant_paths}{RESET}\n")

    print(f"{BOLD}AGENT & REPOSITORY METRICS:{RESET}")
    print(f"  • Files Inspected / Touched: {BOLD}{scenario.stats.files_inspected}{RESET}")
    print(f"  • Functions Analyzed:       {BOLD}{scenario.stats.functions_analyzed}{RESET}")
    print(f"  • Tests Passed:             {GREEN}{BOLD}{scenario.stats.tests_run} / {scenario.stats.tests_run}{RESET}")

    # Jev status
    jev_status = jev_client.test_connection()
    jev_indicator = f"{GREEN}● Live API ({jev_status['latency_ms']}ms){RESET}" if jev_status["connected"] else f"{YELLOW}● Calibrated Engine ({jev_status['latency_ms']}ms){RESET}"
    print(f"  • Jev System 1 Engine:      {jev_indicator}\n")

    # Developer Attention Required
    if scenario.attention_items:
        print(f"{BOLD}{RED}🚨 DEVELOPER ATTENTION REQUIRED ({len(scenario.attention_items)} items):{RESET}")
        for item in scenario.attention_items:
            color = RED if item.level == "HIGH" else YELLOW
            print(f"  {color}[{item.level} RISK]{RESET} {BOLD}{item.title}{RESET}")
            print(f"    {DIM}Detail:{RESET} {item.detail}")
            print(f"    {DIM}Action Required:{RESET} {CYAN}{item.action_required}{RESET}")
            print(f"    {DIM}Jev Attention Probability:{RESET} {BOLD}{int(item.jev_attention_probability * 100)}%{RESET}\n")
    else:
        print(f"{GREEN}✓ No critical breaking risks detected by Jev System 1.{RESET}\n")

    # Logical Changes
    print(f"{BOLD}{MAGENTA}LOGICAL CHANGES & JEV SYSTEM 1 CLASSIFICATION:{RESET}")
    for change in scenario.logical_changes:
        print(f"  {BOLD}• {change.title}{RESET}")
        print(f"    Category: {CYAN}{change.category}{RESET} (Jev confidence: {int(change.confidence_score * 100)}%)")
        print(f"    Blast Radius: {YELLOW}{change.blast_radius}{RESET}")
        if change.affected_services:
            print(f"    Affected Files: {', '.join(change.affected_services)}")
        print(f"    {DIM}Why:{RESET} {change.why_explanation}")

        # List individual diff hunks
        for h in change.hunks[:3]:
            print(f"      {DIM}Hunk:{RESET} {h.file_path} ({h.line_range}) @@ {h.symbol} @@")
            print(f"      {DIM}Jev Triage:{RESET} {h.jev_result.classification.category} | Blast: {h.jev_result.blast_radius.level} | Attention: {h.jev_result.human_review.required}")
        if len(change.hunks) > 3:
            print(f"      {DIM}... and {len(change.hunks) - 3} more hunks{RESET}")
        print()

    # Grounded Concept
    print(f"{BOLD}{GREEN}💡 GROUNDED DEVELOPER LEARNING:{RESET}")
    concept = scenario.grounded_concept
    print(f"  Concept: {BOLD}{concept.name}{RESET}")
    print(f"  Headline: {concept.headline}")
    print(f"  {DIM}Application in this repo:{RESET} {concept.how_your_repo_uses_it}\n")

    print(f"{BOLD}{CYAN}------------------------------------------------------------------------{RESET}")
    print(f"To launch the interactive visual dashboard, run: {BOLD}python -m agent_trace.cli serve{RESET}\n")

def main():
    parser = argparse.ArgumentParser(description="AgentTrace: Developer Control Plane for AI Coding Agents")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze command (RUN ON ANY REPO!)
    analyze_parser = subparsers.add_parser("analyze", help="Analyze any Git repository on your machine")
    analyze_parser.add_argument("repo_path", nargs="?", default=".", help="Path to any Git repository (default: current directory)")
    analyze_parser.add_argument("--repo", type=str, default=None, help="Explicit flag for repository path")
    analyze_parser.add_argument("--diff", type=str, default="auto", help="Git diff target: 'auto', 'uncommitted', 'staged', 'unstaged', 'HEAD~1..HEAD', or branch (default: auto)")
    analyze_parser.add_argument("--prompt", type=str, default=None, help="Optional user request prompt to correlate against")
    analyze_parser.add_argument("--serve", action="store_true", help="Launch the Web Control Plane for this repository after analysis")
    analyze_parser.add_argument("--port", type=int, default=8000, help="Port if --serve is used")

    # report command (Pre-baked scenarios)
    report_parser = subparsers.add_parser("report", help="Generate a Change Intelligence Report for sample scenarios")
    report_parser.add_argument("--scenario", choices=["payment", "auth"], default="payment", help="Sample scenario")

    # serve command
    serve_parser = subparsers.add_parser("serve", help="Launch the local interactive Web Control Plane")
    serve_parser.add_argument("--repo", type=str, default=".", help="Default repo path to inspect in the web UI")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to serve on (default: 8000)")

    # test-jev command
    subparsers.add_parser("test-jev", help="Test live connection to Jev TypeSafe AI API")

    # impact-graph command
    ig_parser = subparsers.add_parser("impact-graph", help="Build semantic impact graph for a repository diff using Serena")
    ig_parser.add_argument("repo_path", nargs="?", default=".", help="Path to a Git repository (default: current directory)")
    ig_parser.add_argument("--repo", type=str, default=None, help="Explicit flag for repository path")
    ig_parser.add_argument("--diff", type=str, default="auto", help="Git diff target (default: auto)")
    ig_parser.add_argument("--depth", type=int, default=2, help="Maximum traversal depth (default: 2)")
    ig_parser.add_argument("--output", type=str, default=None, help="Directory to write graph.json + report.md (optional)")

    # Shortcut: if first argument is a path (not a known command), redirect to analyze
    if len(sys.argv) > 1 and sys.argv[1] not in ("analyze", "report", "serve", "test-jev", "impact-graph", "-h", "--help"):
        target_path = sys.argv[1]
        sys.argv = [sys.argv[0], "analyze", target_path] + sys.argv[2:]

    args = parser.parse_args()

    if args.command == "analyze":
        target = args.repo or args.repo_path or "."
        repo_path = Path(target).expanduser().resolve()
        
        from .engine.live_git import live_git_engine
        live_git_engine.repo_path = repo_path
        if not live_git_engine.is_git_repo():
            print(f"{RED}Error: '{repo_path}' is not a valid git repository or working tree.{RESET}")
            sys.exit(1)

        diff_target = args.diff or "auto"
        print(f"Analyzing repository at {CYAN}{repo_path}{RESET} (mode: {diff_target})...")
        scenario = transcript_watcher.generate_scenario_from_repo(
            repo_path=str(repo_path),
            diff_target=diff_target,
            user_prompt=args.prompt
        )
        print_scenario_report(scenario)

        if args.serve:
            run_server(port=args.port, default_repo=str(repo_path))

    elif args.command == "report":
        scenario = get_payment_retry_scenario() if args.scenario == "payment" else get_auth_scenario()
        print_scenario_report(scenario)

    elif args.command == "serve":
        run_server(port=args.port, default_repo=args.repo)

    elif args.command == "impact-graph":
        target = args.repo or args.repo_path or "."
        repo_path = Path(target).expanduser().resolve()

        from .engine.live_git import live_git_engine
        live_git_engine.repo_path = repo_path
        if not live_git_engine.is_git_repo():
            print(f"{RED}Error: '{repo_path}' is not a valid git repository.{RESET}")
            sys.exit(1)

        from .engine.semantic_impact import get_impact_graph_builder
        from .engine.serena_client import get_serena_client

        serena = get_serena_client()
        available = serena.is_available()
        print(f"\n{BOLD}{CYAN}AgentTrace — Semantic Impact Graph{RESET}")
        print(f"  Repository : {CYAN}{repo_path}{RESET}")
        print(f"  Diff target: {args.diff}")
        print(f"  Max depth  : {args.depth}")
        print(f"  Serena MCP : {GREEN}● available{RESET}" if available else f"  Serena MCP : {YELLOW}● unavailable (AST fallback){RESET}")
        print()

        meta = live_git_engine.get_repo_meta()
        raw_diff, mode_desc = live_git_engine.get_diff(args.diff)
        hunks = live_git_engine.parse_diff_hunks(raw_diff)

        if not hunks:
            print(f"{YELLOW}No diff hunks found for target '{args.diff}'.{RESET}")
            sys.exit(0)

        print(f"  Hunks found: {BOLD}{len(hunks)}{RESET} across {BOLD}{len(set(h.file_path for h in hunks))}{RESET} files")
        print(f"  Building semantic impact graph...\n")

        builder = get_impact_graph_builder(repo_path=str(repo_path), max_depth=args.depth)
        graph = builder.build(
            hunks=hunks,
            change_set_id=meta.get("commit_hash", "live"),
            repository=meta.get("name", "repo"),
        )

        # Terminal summary
        changed = [n for n in graph.nodes if n.change_status in ("added", "modified", "deleted")]
        consumers = [n for n in graph.nodes if n.change_status == "unchanged" and n.node_type == "symbol"]
        unattended = graph.get_unattended_nodes()
        print(f"{BOLD}{GREEN}Impact Graph Built:{RESET}")
        print(f"  Nodes       : {BOLD}{len(graph.nodes)}{RESET}  (changed: {len(changed)}, consumers: {len(consumers)}, unattended: {len(unattended)})")
        print(f"  Edges       : {BOLD}{len(graph.edges)}{RESET}")
        print(f"  Chunks      : {BOLD}{len(graph.chunks)}{RESET}")
        print()

        if changed:
            print(f"{BOLD}{MAGENTA}Changed Symbols:{RESET}")
            for n in changed[:10]:
                status_color = GREEN if n.change_status == "added" else (RED if n.change_status == "deleted" else YELLOW)
                print(f"  {status_color}[{(n.change_status or '').upper()}]{RESET} {BOLD}{n.name}{RESET}  {DIM}({n.file}){RESET}")
            if len(changed) > 10:
                print(f"  {DIM}... and {len(changed) - 10} more{RESET}")
            print()

        if unattended:
            print(f"{BOLD}{YELLOW}⚠️  Unattended Symbols (Omission Risk — untouched in diff):{RESET}")
            for n in unattended[:8]:
                risk = n.data.get("omission_risk", "high").replace("_", " ").upper()
                print(f"  • {BOLD}{YELLOW}{n.name}{RESET}  {DIM}({n.file}) [Risk: {risk}]{RESET}")
            if len(unattended) > 8:
                print(f"  {DIM}... and {len(unattended) - 8} more{RESET}")
            print()

        if consumers:
            print(f"{BOLD}{CYAN}Affected Consumers (references found):{RESET}")
            for n in consumers[:8]:
                conf = n.data.get("confidence", 0)
                src = n.data.get("evidence_source", "?")
                is_un = " [UNATTENDED]" if n in unattended else ""
                print(f"  • {BOLD}{n.name}{RESET}  {DIM}{n.file}  [{src}, conf={conf:.2f}]{is_un}{RESET}")
            if len(consumers) > 8:
                print(f"  {DIM}... and {len(consumers) - 8} more{RESET}")
            print()

        # Optional: write output files
        if args.output:
            out_dir = Path(args.output)
            out_dir.mkdir(parents=True, exist_ok=True)
            import json as _json

            graph_json = out_dir / "graph.json"
            graph_json.write_text(graph.model_dump_json(indent=2))

            nodes_json = out_dir / "nodes.json"
            nodes_json.write_text(_json.dumps([n.model_dump() for n in graph.nodes], indent=2))

            edges_json = out_dir / "edges.json"
            edges_json.write_text(_json.dumps([e.model_dump() for e in graph.edges], indent=2))

            # Markdown report
            md_lines = [
                f"# Semantic Impact Graph — {meta.get('name', 'repo')}",
                f"\n**Diff:** `{args.diff}` | **Depth:** {args.depth} | **Commit:** `{meta.get('commit_hash', 'live')}`\n",
                f"## Summary\n",
                f"- Nodes: {len(graph.nodes)} (changed: {len(changed)}, consumers: {len(consumers)}, unattended: {len(unattended)})",
                f"- Edges: {len(graph.edges)}",
                f"- Chunks: {len(graph.chunks)}\n",
                f"## Changed Symbols\n",
            ]
            for n in changed:
                md_lines.append(f"- `{n.name}` ({n.change_status}) — `{n.file}`")
            if unattended:
                md_lines.append(f"\n## Unattended Symbols (Potential Omission Risks)\n")
                for n in unattended:
                    risk = n.data.get("omission_risk", "high")
                    md_lines.append(f"- ⚠️ `{n.name}` — `{n.file}` _(risk: {risk})_")
            md_lines.append(f"\n## Affected Consumers\n")
            for n in consumers:
                src = n.data.get("evidence_source", "?")
                conf = n.data.get("confidence", 0)
                md_lines.append(f"- `{n.name}` — `{n.file}` _(source: {src}, confidence: {conf:.2f})_")
            md_lines.append(f"\n## Edges\n")
            for e in graph.edges[:30]:
                md_lines.append(f"- `{e.source}` —[{e.relationship}]→ `{e.target}` _(conf: {e.confidence:.2f}, {e.evidence_source})_")
            if len(graph.edges) > 30:
                md_lines.append(f"- _...and {len(graph.edges) - 30} more_")

            report_md = out_dir / "report.md"
            report_md.write_text("\n".join(md_lines))

            print(f"{GREEN}Output written to:{RESET} {CYAN}{out_dir}{RESET}")
            print(f"  • graph.json  ({graph_json.stat().st_size} bytes)")
            print(f"  • nodes.json")
            print(f"  • edges.json")
            print(f"  • report.md")

        print(f"\n{DIM}Launch the visual graph in the Web UI: {BOLD}python -m agent_trace.cli serve{RESET}\n")

    elif args.command == "test-jev":
        print_banner()
        print(f"Pinging Jev TypeSafe AI System One (`https://api.typesafe.ai/v1/systemone`)...")
        res = jev_client.test_connection()
        print(f"Result: {res}")
    else:
        # Default action: analyze current repo
        scenario = transcript_watcher.generate_scenario_from_repo(".", diff_target="auto")
        print_scenario_report(scenario)

if __name__ == "__main__":
    main()
