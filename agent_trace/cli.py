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
    analyze_parser.add_argument("--repo", type=str, default=".", help="Path to any Git repository (default: current directory)")
    analyze_parser.add_argument("--diff", type=str, default="HEAD~1..HEAD", help="Git diff commit range or branch (default: HEAD~1..HEAD)")
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

    args = parser.parse_args()

    if args.command == "analyze":
        repo_path = Path(args.repo).resolve()
        if not (repo_path / ".git").exists() and not (repo_path.parent / ".git").exists():
            print(f"{RED}Error: '{repo_path}' is not a git repository.{RESET}")
            sys.exit(1)

        print(f"Analyzing repository at {CYAN}{repo_path}{RESET} (diff target: {args.diff})...")
        scenario = transcript_watcher.generate_scenario_from_repo(
            repo_path=str(repo_path),
            diff_target=args.diff,
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

    elif args.command == "test-jev":
        print_banner()
        print(f"Pinging Jev TypeSafe AI System One (`https://api.typesafe.ai/v1/systemone`)...")
        res = jev_client.test_connection()
        print(f"Result: {res}")
    else:
        # Default action: analyze current repo
        scenario = transcript_watcher.generate_scenario_from_repo(".", diff_target="HEAD~1..HEAD")
        print_scenario_report(scenario)

if __name__ == "__main__":
    main()
