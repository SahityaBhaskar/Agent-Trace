"""
Lightweight cross-file repository call graph.
Nodes: File, Class, Function/Method
Edges: IMPORTS, CALLS, DEFINES
Built purely from Python ast — no external dependencies.
"""
import ast
import os
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple


@dataclass
class GraphNode:
    id: str                  # e.g. "file:payment.py", "func:PaymentService.charge"
    kind: str                # "file" | "class" | "function" | "method"
    name: str
    file_path: str
    line: int = 0


@dataclass
class GraphEdge:
    source: str              # node id
    target: str              # node id
    relation: str            # "IMPORTS" | "CALLS" | "DEFINES"


@dataclass
class RepoGraph:
    nodes: Dict[str, GraphNode] = field(default_factory=dict)
    edges: List[GraphEdge] = field(default_factory=list)

    def add_node(self, node: GraphNode):
        self.nodes[node.id] = node

    def add_edge(self, source: str, target: str, relation: str):
        self.edges.append(GraphEdge(source=source, target=target, relation=relation))

    def callers_of(self, symbol_name: str) -> List[str]:
        """Return node IDs that CALL the given symbol name."""
        target_ids = {n.id for n in self.nodes.values() if n.name == symbol_name}
        return [e.source for e in self.edges if e.relation == "CALLS" and e.target in target_ids]

    def imports_of(self, file_path: str) -> List[str]:
        """Return file node IDs imported by the given file."""
        src_id = f"file:{file_path}"
        return [e.target for e in self.edges if e.relation == "IMPORTS" and e.source == src_id]

    def to_summary(self) -> dict:
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "files": [n.name for n in self.nodes.values() if n.kind == "file"],
        }


def _extract_file_graph(file_path: str, content: str, graph: RepoGraph):
    """Parse one Python file and add its nodes/edges into graph."""
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return

    fname = os.path.basename(file_path)
    file_node_id = f"file:{file_path}"
    graph.add_node(GraphNode(id=file_node_id, kind="file", name=fname, file_path=file_path))

    # --- IMPORTS edges ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target_id = f"file:{alias.name.replace('.', '/')}.py"
                graph.add_edge(file_node_id, target_id, "IMPORTS")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                target_id = f"file:{node.module.replace('.', '/')}.py"
                graph.add_edge(file_node_id, target_id, "IMPORTS")

    # --- Class / Function / Method DEFINES edges ---
    current_class: Optional[str] = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            current_class = node.name
            class_id = f"class:{file_path}:{node.name}"
            graph.add_node(GraphNode(id=class_id, kind="class", name=node.name,
                                     file_path=file_path, line=node.lineno))
            graph.add_edge(file_node_id, class_id, "DEFINES")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parent = current_class or ""
            kind = "method" if parent else "function"
            full_name = f"{parent}.{node.name}" if parent else node.name
            func_id = f"func:{file_path}:{full_name}"
            graph.add_node(GraphNode(id=func_id, kind=kind, name=full_name,
                                     file_path=file_path, line=node.lineno))
            parent_id = f"class:{file_path}:{parent}" if parent else file_node_id
            graph.add_edge(parent_id, func_id, "DEFINES")

    # --- CALLS edges (simple name-based heuristic) ---
    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # determine caller id
        # walk the function body for Call nodes
        for call in ast.walk(func_node):
            if not isinstance(call, ast.Call):
                continue
            callee_name: Optional[str] = None
            if isinstance(call.func, ast.Name):
                callee_name = call.func.id
            elif isinstance(call.func, ast.Attribute):
                callee_name = call.func.attr
            if not callee_name:
                continue
            # find a matching node by name in graph
            for target_node in list(graph.nodes.values()):
                if target_node.name == callee_name or target_node.name.endswith(f".{callee_name}"):
                    parent2 = current_class or ""
                    full_name2 = f"{parent2}.{func_node.name}" if parent2 else func_node.name
                    caller_id = f"func:{file_path}:{full_name2}"
                    if caller_id in graph.nodes:
                        graph.add_edge(caller_id, target_node.id, "CALLS")
                    break


def build_repo_graph(repo_path: str, max_files: int = 60) -> RepoGraph:
    """
    Walk a repository and build a cross-file graph.
    Only processes .py files; skips venv, __pycache__, .git, node_modules.
    Caps at max_files to stay fast during analysis.
    """
    graph = RepoGraph()
    root = Path(repo_path).expanduser().resolve()
    skip_dirs = {".venv", "venv", "__pycache__", ".git", "node_modules", "dist", "build", ".eggs"}

    py_files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fname in filenames:
            if fname.endswith(".py"):
                py_files.append(Path(dirpath) / fname)
        if len(py_files) >= max_files:
            break

    for fpath in py_files[:max_files]:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            rel = str(fpath.relative_to(root))
            _extract_file_graph(rel, content, graph)
        except Exception:
            continue

    return graph


# Module-level singleton — rebuilt on demand
_cached_graph: Optional[RepoGraph] = None
_cached_repo_path: str = ""
_cache_lock = threading.Lock()


def get_repo_graph(repo_path: str) -> RepoGraph:
    """Return a cached RepoGraph for the given path, rebuilding if path changes.

    Thread-safe: cache reads/writes are protected by _cache_lock so concurrent
    HTTP requests under ThreadingHTTPServer cannot race on the global state.
    """
    global _cached_graph, _cached_repo_path
    with _cache_lock:
        if _cached_graph is None or _cached_repo_path != repo_path:
            _cached_graph = build_repo_graph(repo_path)
            _cached_repo_path = repo_path
        return _cached_graph
