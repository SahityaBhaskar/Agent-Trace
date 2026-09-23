import os
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from ..models.report import DiffHunk
from ..models.jev_types import JevEvaluationResult, JevClassificationResult, JevBlastRadiusResult, JevHumanReviewResult, JevBreakingRiskResult
from .jev_client import jev_client
from .ast_analyzer import ast_analyzer

class LiveGitEngine:
    """Production Git Analyzer: Inspects live Git repositories, extracts hunks, and runs Jev evaluations."""

    def __init__(self, repo_path: str = "."):
        self.repo_path = Path(repo_path).resolve()

    def is_git_repo(self) -> bool:
        return (self.repo_path / ".git").exists() or subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=self.repo_path,
            capture_output=True,
            text=True
        ).returncode == 0

    def get_diff(self, target: str = "HEAD~1..HEAD") -> str:
        """Fetches raw git diff for commit range or working tree."""
        cmd = ["git", "diff", target] if ".." in target else ["git", "diff", target]
        res = subprocess.run(
            cmd,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=False
        )
        if res.returncode != 0:
            # Fall back to working tree diff
            res = subprocess.run(["git", "diff"], cwd=self.repo_path, capture_output=True, text=True)
        return res.stdout

    def parse_diff_hunks(self, raw_diff: str) -> List[DiffHunk]:
        """Parses unified git diff output into structured DiffHunk models."""
        hunks: List[DiffHunk] = []
        if not raw_diff.strip():
            return hunks

        file_diffs = raw_diff.split("diff --git ")
        hunk_idx = 0

        for fdiff in file_diffs:
            if not fdiff.strip():
                continue

            lines = fdiff.split("\n")
            first_line = lines[0]
            # Match paths: a/path b/path
            path_match = re.search(r"a/(.+?)\s+b/(.+)", first_line)
            file_path = path_match.group(2) if path_match else "unknown_file"

            # Skip binary files or lockfiles
            if any(file_path.endswith(ext) for ext in [".lock", "-lock.json", ".png", ".jpg", ".pyc"]):
                continue

            # Split into individual hunks: @@ -start,len +start,len @@
            raw_hunk_blocks = re.split(r"(@@\s+-[0-9,]+\s+\+[0-9,]+\s+@@)", fdiff)
            for i in range(1, len(raw_hunk_blocks), 2):
                hunk_header = raw_hunk_blocks[i]
                hunk_body = raw_hunk_blocks[i + 1] if i + 1 < len(raw_hunk_blocks) else ""

                line_match = re.search(r"\+([0-9]+)(?:,([0-9]+))?", hunk_header)
                start_line = int(line_match.group(1)) if line_match else 1
                length = int(line_match.group(2)) if line_match and line_match.group(2) else 1
                line_range = f"Lines {start_line}–{start_line + length}"

                old_lines = []
                new_lines = []
                for hline in hunk_body.split("\n"):
                    if hline.startswith("-") and not hline.startswith("---"):
                        old_lines.append(hline[1:])
                    elif hline.startswith("+") and not hline.startswith("+++"):
                        new_lines.append(hline[1:])

                old_text = "\n".join(old_lines)
                new_text = "\n".join(new_lines)

                # Heuristic symbol detection
                symbol = "module"
                for nline in new_lines:
                    if "class " in nline:
                        symbol = nline.strip().split("class ")[1].split("(")[0].split(":")[0].strip()
                        break
                    elif "def " in nline:
                        symbol = nline.strip().split("def ")[1].split("(")[0].strip()
                        break
                    elif "function " in nline:
                        symbol = nline.strip().split("function ")[1].split("(")[0].strip()
                        break

                hunk_idx += 1
                hunk_id = f"live_hunk_{hunk_idx}"

                # Real-time Jev System 1 evaluation
                jev_res = jev_client.evaluate_hunk(
                    hunk_id=hunk_id,
                    file_path=file_path,
                    diff_text=f"- {old_text}\n+ {new_text}",
                    agent_context=f"Git modification in {file_path}",
                    fallback_category="REFACTOR"
                )

                hunks.append(DiffHunk(
                    id=hunk_id,
                    file_path=file_path,
                    symbol=symbol,
                    old_lines=old_text,
                    new_lines=new_text,
                    line_range=line_range,
                    change_type="MODIFIED" if old_text and new_text else ("ADDED" if new_text else "DELETED"),
                    jev_result=jev_res,
                    evidence_event_ids=[]
                ))

        return hunks

live_git_engine = LiveGitEngine()
