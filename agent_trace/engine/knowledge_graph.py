"""
Developer Knowledge Graph — lightweight JSON-file persistence.
Tracks concepts encountered across agent sessions.
No external dependencies — uses stdlib json + pathlib.
"""
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class ConceptEntry:
    name: str
    category: str
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    encounter_count: int = 1
    repos: List[str] = field(default_factory=list)
    headlines: List[str] = field(default_factory=list)   # last 3 headlines


@dataclass
class KnowledgeGraph:
    concepts: Dict[str, ConceptEntry] = field(default_factory=dict)


_DEFAULT_PATH = Path.home() / ".agenttrace" / "knowledge_graph.json"


def _load(path: Path) -> KnowledgeGraph:
    if not path.exists():
        return KnowledgeGraph()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        concepts = {}
        for name, entry in raw.get("concepts", {}).items():
            concepts[name] = ConceptEntry(**entry)
        return KnowledgeGraph(concepts=concepts)
    except Exception:
        return KnowledgeGraph()


def _save(kg: KnowledgeGraph, path: Path):
    """Write knowledge graph atomically — writes to a .tmp file then os.replace()
    so concurrent readers never observe a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"concepts": {k: asdict(v) for k, v in kg.concepts.items()}}
    tmp_path = Path(str(path) + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def record_concept(
    concept_name: str,
    category: str,
    repo_name: str = "",
    headline: str = "",
    path: Optional[Path] = None,
) -> ConceptEntry:
    """Record a concept encounter. Creates or updates the persisted entry."""
    store_path = path or _DEFAULT_PATH
    kg = _load(store_path)

    key = concept_name.lower().strip()
    now = time.time()
    if key in kg.concepts:
        entry = kg.concepts[key]
        entry.last_seen = now
        entry.encounter_count += 1
        if repo_name and repo_name not in entry.repos:
            entry.repos.append(repo_name)
        if headline:
            entry.headlines = ([headline] + entry.headlines)[:3]
    else:
        entry = ConceptEntry(
            name=concept_name,
            category=category,
            first_seen=now,
            last_seen=now,
            encounter_count=1,
            repos=[repo_name] if repo_name else [],
            headlines=[headline] if headline else [],
        )
        kg.concepts[key] = entry

    _save(kg, store_path)
    return entry


def get_knowledge_graph(path: Optional[Path] = None) -> dict:
    """Return the full knowledge graph as a serialisable dict."""
    store_path = path or _DEFAULT_PATH
    kg = _load(store_path)
    # Sort by encounter_count desc
    sorted_concepts = sorted(kg.concepts.values(), key=lambda c: c.encounter_count, reverse=True)
    return {
        "concepts": [asdict(c) for c in sorted_concepts],
        "total_unique_concepts": len(kg.concepts),
    }
