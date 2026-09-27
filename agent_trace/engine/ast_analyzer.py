import re
import ast
from typing import Dict, Any, List, Optional

class AstAnalyzer:
    """Lightweight AST and Symbol Boundary Analyzer for Python, TypeScript, and JavaScript."""

    def extract_symbols_from_source(self, code: str, file_path: str) -> List[Dict[str, Any]]:
        """Extracts top-level and class-level symbols with start/end lines."""
        symbols = []

        if file_path.endswith(".py"):
            try:
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        symbols.append({
                            "name": node.name,
                            "type": "CLASS" if isinstance(node, ast.ClassDef) else "FUNCTION",
                            "start_line": getattr(node, "lineno", 1),
                            "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                            "file_path": file_path,
                        })
                return symbols
            except Exception:
                pass

        # Regex symbol parser for TypeScript/JavaScript/Python fallback
        lines = code.split("\n")
        class_regex = re.compile(r"^(?:export\s+)?class\s+([A-Za-z0-9_]+)")
        func_regex = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)")
        method_regex = re.compile(r"^\s+(?:async\s+)?([A-Za-z0-9_]+)\s*\([^)]*\)\s*[{:]")

        current_class = None
        for i, line in enumerate(lines, 1):
            c_match = class_regex.match(line)
            if c_match:
                current_class = c_match.group(1)
                symbols.append({
                    "name": current_class,
                    "type": "CLASS",
                    "start_line": i,
                    "end_line": i,
                    "file_path": file_path,
                })
                continue

            f_match = func_regex.match(line)
            if f_match:
                symbols.append({
                    "name": f_match.group(1),
                    "type": "FUNCTION",
                    "start_line": i,
                    "end_line": i,
                    "file_path": file_path,
                })
                continue

            m_match = method_regex.match(line)
            if m_match and current_class:
                symbols.append({
                    "name": f"{current_class}.{m_match.group(1)}",
                    "type": "METHOD",
                    "start_line": i,
                    "end_line": i,
                    "file_path": file_path,
                })

        return symbols

    def find_enclosing_symbol(self, symbols: List[Dict[str, Any]], line_number: int) -> Optional[str]:
        """Finds the most specific symbol enclosing a given line number."""
        for s in reversed(symbols):
            if s["start_line"] <= line_number <= s.get("end_line", s["start_line"] + 50):
                return s["name"]
        return None

    def semantic_diff(self, before_source: str, after_source: str, file_path: str) -> dict:
        """
        Compare two versions of a source file at the AST symbol level.
        Returns a dict with:
          - added: list of symbol names added
          - removed: list of symbol names removed
          - modified: list of symbol names present in both but with different line counts (heuristic for body change)
          - summary: human-readable one-liner describing the dominant change
        """
        def _symbols(src: str) -> dict:
            """Return {name: end_line - start_line} for every function/class in src."""
            result = {}
            if not src:
                return result
            syms = self.extract_symbols_from_source(src, file_path)
            for s in syms:
                result[s["name"]] = s.get("end_line", s["start_line"]) - s["start_line"]
            return result

        before = _symbols(before_source)
        after = _symbols(after_source)

        before_names = set(before)
        after_names = set(after)

        added = sorted(after_names - before_names)
        removed = sorted(before_names - after_names)
        modified = sorted(
            n for n in before_names & after_names
            if abs(before[n] - after[n]) > 2  # body grew/shrank by >2 lines
        )

        parts = []
        if added:
            parts.append(f"Added: {', '.join(added[:3])}")
        if removed:
            parts.append(f"Removed: {', '.join(removed[:3])}")
        if modified:
            parts.append(f"Modified: {', '.join(modified[:3])}")
        if not parts:
            summary = "No symbol-level changes detected (formatting or comment change)."
        else:
            summary = "; ".join(parts) + f" in {file_path.split('/')[-1]}."

        return {
            "added": added,
            "removed": removed,
            "modified": modified,
            "summary": summary,
        }

ast_analyzer = AstAnalyzer()
