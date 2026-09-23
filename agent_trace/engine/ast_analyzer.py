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

ast_analyzer = AstAnalyzer()
