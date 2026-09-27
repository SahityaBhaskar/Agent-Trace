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
        self.repo_path = Path(repo_path).expanduser().resolve()

    def _run_git(self, args: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        target_dir = cwd or self.repo_path
        try:
            return subprocess.run(
                ["git"] + args,
                cwd=target_dir,
                capture_output=True,
                text=True,
                env=env,
                check=False
            )
        except Exception as e:
            return subprocess.CompletedProcess(["git"] + args, 1, "", str(e))

    def is_git_repo(self) -> bool:
        res = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return res.returncode == 0

    def get_repo_root(self) -> Path:
        res = self._run_git(["rev-parse", "--show-toplevel"])
        if res.returncode == 0 and res.stdout.strip():
            return Path(res.stdout.strip())
        return self.repo_path

    def get_repo_meta(self) -> Dict[str, str]:
        """Returns repo name, branch, and latest commit info."""
        root = self.get_repo_root()
        branch_res = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
        branch = branch_res.stdout.strip() if branch_res.returncode == 0 else "main"

        commit_res = self._run_git(["log", "-1", "--format=%h|%s"], cwd=root)
        commit_hash, commit_msg = ("clean", "No commits yet")
        if commit_res.returncode == 0 and commit_res.stdout.strip():
            parts = commit_res.stdout.strip().split("|", 1)
            commit_hash = parts[0]
            commit_msg = parts[1] if len(parts) > 1 else ""

        return {
            "name": root.name,
            "root": str(root),
            "branch": branch,
            "commit_hash": commit_hash,
            "commit_msg": commit_msg
        }

    def _get_untracked_files_diff(self, root: Path) -> str:
        """Finds untracked files and turns them into unified diff format."""
        status_res = self._run_git(["status", "--porcelain"], cwd=root)
        if status_res.returncode != 0 or not status_res.stdout.strip():
            return ""

        synthetic_diffs = []
        ignored_exts = {".pyc", ".png", ".jpg", ".jpeg", ".lock", ".zip", ".tar", ".gz", ".db", ".sqlite", ".DS_Store"}
        ignored_dirs = {"node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build"}

        for line in status_res.stdout.split("\n"):
            line = line.strip()
            if not line.startswith("?? "):
                continue
            rel_path = line[3:].strip().strip('"')
            
            # Skip ignored paths
            parts = Path(rel_path).parts
            if any(p in ignored_dirs for p in parts):
                continue
            if any(rel_path.endswith(ext) for ext in ignored_exts):
                continue

            full_file = root / rel_path
            if not full_file.is_file():
                continue

            try:
                # Limit size to 256KB to avoid massive dumps
                if full_file.stat().st_size > 256 * 1024:
                    continue
                content = full_file.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()
                count = len(lines)
                plus_lines = "\n".join("+" + l for l in lines)
                synthetic = (
                    f"diff --git a/{rel_path} b/{rel_path}\n"
                    f"new file mode 100644\n"
                    f"--- /dev/null\n"
                    f"+++ b/{rel_path}\n"
                    f"@@ -0,0 +1,{count} @@\n"
                    f"{plus_lines}\n"
                )
                synthetic_diffs.append(synthetic)
            except Exception:
                continue

        return "\n".join(synthetic_diffs)

    def get_diff(self, target: str = "auto") -> tuple[str, str]:
        """
        Fetches raw git diff for commit range or working tree.
        Returns: (raw_diff, detected_mode_description)
        """
        root = self.get_repo_root()
        mode_desc = target
        t = target.strip()
        lowered = t.lower()

        # 1. Staged only
        if lowered in ("staged", "cached", "--staged", "--cached"):
            res = self._run_git(["diff", "--staged"], cwd=root)
            return res.stdout, "Staged Changes"

        # 2. Unstaged only
        if lowered in ("unstaged", "working-tree-unstaged"):
            res = self._run_git(["diff"], cwd=root)
            return res.stdout, "Unstaged Working Tree Changes"

        # 3. All uncommitted (staged + unstaged + untracked)
        if lowered in ("uncommitted", "working", "all-uncommitted"):
            res = self._run_git(["diff", "HEAD"], cwd=root)
            diff_text = res.stdout
            untracked = self._get_untracked_files_diff(root)
            if untracked:
                diff_text = (diff_text + "\n" + untracked).strip()
            return diff_text, "All Uncommitted Changes (Working Tree + Staged + Untracked)"

        # 4. Explicit commit target or range (e.g. main..feature, HEAD~1..HEAD, sha)
        if lowered not in ("auto", "default", ""):
            res = self._run_git(["diff", t], cwd=root)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout, f"Commit Diff: {t}"
            # Try git show if single commit sha/ref was passed
            show_res = self._run_git(["show", "--format=", t], cwd=root)
            if show_res.returncode == 0 and show_res.stdout.strip():
                return show_res.stdout, f"Commit {t}"

        # 5. Smart AUTO mode:
        # Check uncommitted modifications first
        head_diff = self._run_git(["diff", "HEAD"], cwd=root).stdout
        untracked = self._get_untracked_files_diff(root)
        combined = (head_diff + "\n" + untracked).strip()
        if combined:
            return combined, "Active Working Tree Changes (Uncommitted)"

        # Working tree is clean: Inspect latest commit
        latest_commit_diff = self._run_git(["diff", "HEAD~1..HEAD"], cwd=root)
        if latest_commit_diff.returncode == 0 and latest_commit_diff.stdout.strip():
            meta = self.get_repo_meta()
            return latest_commit_diff.stdout, f"Latest Commit: {meta['commit_hash']} (\"{meta['commit_msg']}\")"

        # If repo only has initial commit (HEAD~1 fails)
        initial_commit = self._run_git(["show", "--format=", "HEAD"], cwd=root)
        if initial_commit.returncode == 0 and initial_commit.stdout.strip():
            meta = self.get_repo_meta()
            return initial_commit.stdout, f"Initial Commit: {meta['commit_hash']} (\"{meta['commit_msg']}\")"

        # Clean repo with no changes
        return "", "Clean repository (no uncommitted changes or commits detected)"

    def parse_diff_hunks(self, raw_diff: str) -> List[DiffHunk]:
        """Parses unified git diff output into structured DiffHunk models."""
        hunks: List[DiffHunk] = []
        if not raw_diff.strip():
            return hunks

        # Split by file boundary anchored at start of line
        file_diffs = re.split(r"(?:^|\n)diff --git\s+", raw_diff)
        hunk_idx = 0
        # Per-call cache: file_path -> list of symbols (avoid re-parsing same file)
        _symbol_cache: Dict[str, Any] = {}
        root = self.get_repo_root()

        for fdiff in file_diffs:
            if not fdiff.strip():
                continue

            lines = fdiff.split("\n")
            first_line = lines[0]
            # Match paths: a/path b/path, or +++ b/path, or --- a/path
            path_match = re.search(r"a/(.+?)\s+b/([^\s]+)", first_line)
            if path_match:
                file_path = path_match.group(2).strip()
            else:
                plus_match = re.search(r"\+\+\+\s+b/([^\s]+)", fdiff)
                if plus_match and not plus_match.group(1).strip().startswith("/dev/null"):
                    file_path = plus_match.group(1).strip()
                else:
                    minus_match = re.search(r"---\s+a/([^\s]+)", fdiff)
                    file_path = minus_match.group(1).strip() if minus_match else "unknown_file"

            file_path = file_path.strip() if file_path else "unknown_file"

            # Skip binary files or lockfiles
            if any(file_path.endswith(ext) for ext in [".lock", "-lock.json", ".png", ".jpg", ".pyc"]):
                continue

            # Build AST symbol list for this file (cached per parse_diff_hunks call)
            if file_path not in _symbol_cache:
                try:
                    git_res = self._run_git(["show", f"HEAD:{file_path}"], cwd=root)
                    if git_res.returncode == 0:
                        file_content = git_res.stdout
                    else:
                        disk_path = self.repo_path / file_path
                        file_content = disk_path.read_text(encoding="utf-8", errors="replace")
                    _symbol_cache[file_path] = ast_analyzer.extract_symbols_from_source(file_content, file_path)
                except Exception:
                    _symbol_cache[file_path] = []

            file_symbols = _symbol_cache[file_path]

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

                # AST-first symbol resolution, fallback to regex heuristic
                symbol = None
                try:
                    symbol = ast_analyzer.find_enclosing_symbol(file_symbols, start_line)
                except Exception:
                    symbol = None

                if symbol is None:
                    # Regex heuristic fallback
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
