"""
DiffSymbolMapper — maps diff chunks to precise semantic symbols.

Pipeline per chunk:
  1. Serena get_symbols_overview(file)  → structured symbol list with line ranges
  2. Find symbols whose range overlaps the changed line range
  3. If Serena unavailable → fall back to AstAnalyzer.extract_symbols_from_source
  4. If AST unavailable  → fall back to regex heuristic from live_git
  5. If nothing works     → emit UNRESOLVED_CODE_REGION

Every result carries evidence_source + confidence so callers can distinguish
semantic vs. heuristic relationships downstream.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ..models.impact_graph import ImpactNode, ChangeStatus, EvidenceSource
from ..engine.ast_analyzer import ast_analyzer

if TYPE_CHECKING:
    from ..engine.serena_client import SerenaClient

# Confidence constants (re-exported for callers)
CONF_SERENA   = 0.97
CONF_AST      = 0.90
CONF_HEURISTIC = 0.50
CONF_UNRESOLVED = 0.10


def _make_symbol_id(file_path: str, symbol_name: str) -> str:
    """Canonical node ID: 'file::symbol_name'."""
    return f"{file_path}::{symbol_name}"


def _make_file_id(file_path: str) -> str:
    return f"file:{file_path}"


def _make_chunk_id(change_set_id: str, file_path: str, chunk_index: int) -> str:
    return f"chunk:{change_set_id}:{file_path}:{chunk_index}"


# ── Serena symbol parser (reuses SerenaClient.parse_symbols_overview) ─────────

def _serena_symbols_for_file(
    serena: "SerenaClient",
    file_path: str,
) -> List[Dict[str, Any]]:
    """
    Query Serena and parse the symbol overview for one file.
    Returns [] on any failure.
    """
    try:
        raw = serena.get_symbols_overview(file_path)
        if raw:
            from ..engine.serena_client import SerenaClient as _SC
            return _SC.parse_symbols_overview(raw)
    except Exception:
        pass
    return []


def _find_enclosing_symbols(
    symbols: List[Dict[str, Any]],
    start_line: int,
    end_line: int,
) -> List[Dict[str, Any]]:
    """
    Return all symbols whose [start_line, end_line] range overlaps [start_line, end_line].
    A symbol with start_line=0 (unknown) is skipped.
    Prefers the most specific (smallest) enclosing symbol.
    """
    overlapping = []
    for sym in symbols:
        s = sym.get("start_line", 0)
        e = sym.get("end_line", 0) or s
        if s == 0:
            continue
        # overlap check
        if s <= end_line and e >= start_line:
            overlapping.append(sym)
    if not overlapping:
        return []
    # Sort by specificity: smallest span first (most specific)
    overlapping.sort(key=lambda x: (x.get("end_line", 0) - x.get("start_line", 0)))
    return overlapping


# ── Main mapper ───────────────────────────────────────────────────────────────

class DiffSymbolMapper:
    """
    Maps a diff chunk (file + changed line range) to a list of ImpactNode objects
    representing the changed symbols.

    Fallback chain:
      Level 1 → Serena semantic symbols
      Level 2 → AST analysis (Python only)
      Level 3 → regex heuristic
      Level 4 → UNRESOLVED_CODE_REGION
    """

    def __init__(self, serena: Optional["SerenaClient"] = None):
        self._serena = serena

    def map_chunk_to_symbols(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        change_status: ChangeStatus,
        change_set_id: str,
        chunk_index: int,
        repo_root: Optional[str] = None,
    ) -> List[ImpactNode]:
        """
        Return ImpactNode objects for the symbols that overlap [start_line, end_line]
        in file_path.  At minimum returns one UNRESOLVED node.
        """
        # ── Level 1: Serena ──────────────────────────────────────────────────
        if self._serena and self._serena.is_available():
            try:
                serena_syms = _serena_symbols_for_file(self._serena, file_path)
                if serena_syms:
                    matches = _find_enclosing_symbols(serena_syms, start_line, end_line)
                    if matches:
                        return [
                            self._sym_to_node(s, file_path, change_status, "serena", CONF_SERENA)
                            for s in matches
                        ]
            except Exception:
                pass

        # ── Level 2: AST (Python) ────────────────────────────────────────────
        source = self._read_file(file_path, repo_root)
        if source and file_path.endswith(".py"):
            try:
                ast_syms = ast_analyzer.extract_symbols_from_source(source, file_path)
                if ast_syms:
                    matches = _find_enclosing_symbols(ast_syms, start_line, end_line)
                    if matches:
                        return [
                            self._sym_to_node(s, file_path, change_status, "ast", CONF_AST)
                            for s in matches
                        ]
            except Exception:
                pass

        # ── Level 3: Regex heuristic ─────────────────────────────────────────
        if source:
            heuristic_sym = self._heuristic_symbol(source, start_line)
            if heuristic_sym:
                return [self._make_heuristic_node(heuristic_sym, file_path, change_status)]

        # ── Level 4: Unresolved ──────────────────────────────────────────────
        return [self._make_unresolved_node(file_path, start_line, end_line, change_status)]

    def make_file_node(self, file_path: str, change_status: ChangeStatus) -> ImpactNode:
        return ImpactNode(
            id=_make_file_id(file_path),
            node_type="file",
            name=file_path.split("/")[-1],
            kind="file",
            file=file_path,
            change_status=change_status,
        )

    def make_chunk_node(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        change_set_id: str,
        chunk_index: int,
        change_status: ChangeStatus,
    ) -> ImpactNode:
        chunk_id = _make_chunk_id(change_set_id, file_path, chunk_index)
        return ImpactNode(
            id=chunk_id,
            node_type="change_chunk",
            name=f"Chunk #{chunk_index}: {file_path.split('/')[-1]} L{start_line}–{end_line}",
            kind="change_chunk",
            file=file_path,
            start_line=start_line,
            end_line=end_line,
            change_status=change_status,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _sym_to_node(
        sym: Dict[str, Any],
        file_path: str,
        change_status: ChangeStatus,
        source: EvidenceSource,
        confidence: float,
    ) -> ImpactNode:
        name = sym.get("name", "unknown")
        sym_file = sym.get("file") or file_path
        return ImpactNode(
            id=_make_symbol_id(sym_file or file_path, name),
            node_type="symbol",
            name=name,
            kind=sym.get("kind") or sym.get("type", "symbol"),
            file=sym_file or file_path,
            start_line=sym.get("start_line"),
            end_line=sym.get("end_line"),
            change_status=change_status,
            data={"evidence_source": source, "confidence": confidence},
        )

    @staticmethod
    def _make_heuristic_node(name: str, file_path: str, change_status: ChangeStatus) -> ImpactNode:
        return ImpactNode(
            id=_make_symbol_id(file_path, name),
            node_type="symbol",
            name=name,
            kind="symbol",
            file=file_path,
            change_status=change_status,
            data={"evidence_source": "heuristic", "confidence": CONF_HEURISTIC},
        )

    @staticmethod
    def _make_unresolved_node(
        file_path: str, start_line: int, end_line: int, change_status: ChangeStatus
    ) -> ImpactNode:
        return ImpactNode(
            id=_make_symbol_id(file_path, f"UNRESOLVED_L{start_line}_{end_line}"),
            node_type="symbol",
            name=f"UNRESOLVED_CODE_REGION (L{start_line}–{end_line})",
            kind="unresolved",
            file=file_path,
            start_line=start_line,
            end_line=end_line,
            change_status=change_status,
            data={"evidence_source": "unresolved", "confidence": CONF_UNRESOLVED},
        )

    @staticmethod
    def _heuristic_symbol(source: str, start_line: int) -> Optional[str]:
        """Scan lines near start_line for a class/function/def declaration."""
        lines = source.splitlines()
        patterns = [
            re.compile(r"^(?:export\s+)?(?:async\s+)?(?:function|def)\s+([A-Za-z_][A-Za-z0-9_]*)"),
            re.compile(r"^(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ]
        # scan from start_line upward (find enclosing definition)
        scan_start = max(0, start_line - 1)
        for i in range(scan_start, max(-1, scan_start - 30), -1):
            if i >= len(lines):
                continue
            line = lines[i].strip()
            for p in patterns:
                m = p.match(line)
                if m:
                    return m.group(1)
        return None

    @staticmethod
    def _read_file(file_path: str, repo_root: Optional[str]) -> Optional[str]:
        """Try to read file content for fallback analysis."""
        candidates = [Path(file_path)]
        if repo_root:
            candidates.insert(0, Path(repo_root) / file_path)
        for p in candidates:
            try:
                if p.exists():
                    return p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
        return None
