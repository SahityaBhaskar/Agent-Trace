"""
Architectural Impact Flow Engine.

Transforms low-level symbol-level AST/diff graphs and scenario causal trails into
an intuitive, multi-stage Architectural Directed Acyclic Graph (DAG) for developers.

Stages:
  1. Trigger & Intent       - What prompted this change
  2. Core Abstractions      - Newly created or heavily refactored abstractions
  3. Integrated Services    - Modified entrypoints, caller services, and handlers
  4. Blast Radius           - Downstream consumers & unattended omission risks
  5. Invariants & Safety    - Verified contracts, checklist items, and safety gates
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from ..models.report import ScenarioData, DiffHunk, LogicalChange
from ..models.impact_graph import SemanticImpactGraph
from .gemini_client import gemini_client


class ArchitecturalNode(BaseModel):
    id: str
    stage: str                          # 'trigger' | 'abstraction' | 'service' | 'consumer' | 'safety'
    title: str
    subtitle: str = ""
    icon: str = "lightbulb"             # Material Symbols icon name
    badge: str = ""
    badge_color: str = "brand"          # 'brand' | 'cyan' | 'lime' | 'amber' | 'rose' | 'slate'
    status: str = "neutral"             # 'trigger' | 'added' | 'modified' | 'unattended' | 'handled' | 'safety'
    details: str = ""
    symbols: List[str] = Field(default_factory=list)
    file_path: Optional[str] = None
    line_range: Optional[str] = None
    risk_level: Optional[str] = None    # 'none' | 'low' | 'medium' | 'high' | 'critical'
    jev_category: Optional[str] = None  # 'COMMONIZATION' | 'REFACTOR' | 'FEATURE' | etc.
    stats: Dict[str, Any] = Field(default_factory=dict)


class ArchitecturalEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str = ""
    type: str = "calls"                 # 'triggers' | 'centralizes' | 'calls' | 'omission_risk' | 'verifies'
    style: str = "solid"                # 'solid' | 'dashed' | 'alert'
    animated: bool = False


class ArchitecturalStage(BaseModel):
    id: str
    title: str
    subtitle: str
    icon: str
    order: int


class ArchitecturalGraphResponse(BaseModel):
    narrative: str
    invariants: List[str] = Field(default_factory=list)
    blast_level: str = "localized"      # 'localized' | 'service_level' | 'systemic'
    unattended_count: int = 0
    stages: List[ArchitecturalStage] = Field(default_factory=list)
    nodes: List[ArchitecturalNode] = Field(default_factory=list)
    edges: List[ArchitecturalEdge] = Field(default_factory=list)
    source: str = "mind_map_synthesizer"


class ArchitecturalImpactEngine:
    """
    Synthesizes a high-level Developer Architectural Mind Map.
    Zero code syntax, zero line numbers, zero raw file paths on the map.
    Represents the mental model:
      Problem & Catalyst -> Architectural Pattern -> Capabilities Impacted -> Watchouts -> Safety Guarantees
    """

    DEFAULT_STAGES: List[ArchitecturalStage] = [
        ArchitecturalStage(id="trigger", title="1 · Problem & Catalyst", subtitle="Origin goal & intent", icon="lightbulb", order=1),
        ArchitecturalStage(id="abstraction", title="2 · Architectural Strategy", subtitle="Design pattern & approach", icon="architecture", order=2),
        ArchitecturalStage(id="service", title="3 · Capabilities Impacted", subtitle="Enhanced user workflows", icon="hub", order=3),
        ArchitecturalStage(id="consumer", title="4 · Watchouts & Gotchas", subtitle="Risks & downstream attention", icon="warning", order=4),
        ArchitecturalStage(id="safety", title="5 · Safety Guarantees", subtitle="Contracts & verification", icon="verified", order=5),
    ]

    def build_from_scenario(self, scenario: ScenarioData, impact_graph: Optional[SemanticImpactGraph] = None) -> ArchitecturalGraphResponse:
        """Constructs an uncluttered conceptual mind map from scenario causal data."""
        # 1. Extract high-level themes
        prompt = scenario.user_prompt or scenario.title or "Feature implementation"
        
        # Files touched (for high-level context only)
        files_touched = set()
        primary_abstractions = []
        if scenario.logical_changes:
            for ch in scenario.logical_changes:
                if ch.hunks:
                    for h in ch.hunks:
                        if h.file_path:
                            files_touched.add(h.file_path)
                        if h.symbol and h.symbol != "unknown" and h.symbol not in primary_abstractions:
                            primary_abstractions.append(h.symbol)

        # Unattended callers
        unattended_callers = []
        for att in scenario.attention_items:
            if "unattended" in att.title.lower() or "omission" in att.title.lower() or att.level == "HIGH":
                unattended_callers.append(att.title)

        if impact_graph:
            unatt_nodes = impact_graph.get_unattended_nodes()
            for un in unatt_nodes:
                if un.name not in unattended_callers:
                    unattended_callers.append(un.name)

        blast = "localized"
        if scenario.logical_changes and scenario.logical_changes[0].blast_radius:
            blast = scenario.logical_changes[0].blast_radius

        # 2. Synthesize conceptual mind map
        mind_map = gemini_client.synthesize_architectural_mind_map(
            prompt=prompt,
            files_modified=list(files_touched),
            abstractions=primary_abstractions,
            unattended_callers=unattended_callers,
            blast_level=blast,
        )

        return self._assemble_mind_map(
            mind_map=mind_map,
            scenario_id=scenario.id,
            files_count=len(files_touched),
            unattended_count=len(unattended_callers),
            blast_level=blast,
            extra_concept=scenario.grounded_concept.name if scenario.grounded_concept else None,
            extra_concept_desc=scenario.grounded_concept.what_it_is if scenario.grounded_concept else None,
        )

    def build_from_live_diff(
        self,
        hunks: List[DiffHunk],
        impact_graph: Optional[SemanticImpactGraph] = None,
        user_prompt: Optional[str] = None,
    ) -> ArchitecturalGraphResponse:
        """Constructs an uncluttered conceptual mind map for live working tree changes."""
        files_touched = list({h.file_path for h in hunks if h.file_path})
        symbols = [h.symbol for h in hunks if h.symbol and h.symbol != "unknown"]
        prompt = user_prompt or "Active Working Tree Changes"

        unattended_callers = []
        if impact_graph:
            unatt_nodes = impact_graph.get_unattended_nodes()
            for un in unatt_nodes:
                unattended_callers.append(un.name)

        blast = "service_level" if len(files_touched) > 2 else "localized"

        mind_map = gemini_client.synthesize_architectural_mind_map(
            prompt=prompt,
            files_modified=files_touched,
            abstractions=symbols[:4],
            unattended_callers=unattended_callers,
            blast_level=blast,
        )

        return self._assemble_mind_map(
            mind_map=mind_map,
            scenario_id="live-working-tree",
            files_count=len(files_touched),
            unattended_count=len(unattended_callers),
            blast_level=blast,
        )

    def _assemble_mind_map(
        self,
        mind_map: Dict[str, Any],
        scenario_id: str,
        files_count: int,
        unattended_count: int,
        blast_level: str,
        extra_concept: Optional[str] = None,
        extra_concept_desc: Optional[str] = None,
    ) -> ArchitecturalGraphResponse:
        """Assembles 5 clean mind map nodes and directional connecting edges."""
        nodes: List[ArchitecturalNode] = []
        edges: List[ArchitecturalEdge] = []

        # ── Node 1: Catalyst & Goal (Stage 1) ──
        cat_data = mind_map.get("catalyst", {})
        node_cat = ArchitecturalNode(
            id="node_catalyst",
            stage="trigger",
            title=cat_data.get("title", "Core System Goal"),
            subtitle="Business Intent & Driver",
            icon="lightbulb",
            badge="ORIGIN GOAL",
            badge_color="brand",
            status="trigger",
            details=cat_data.get("details", "Targeted enhancement to improve system capabilities and operational resilience."),
            stats={"scope": f"{files_count} module(s) touched"},
        )
        nodes.append(node_cat)

        # ── Node 2: Architectural Strategy (Stage 2) ──
        strat_data = mind_map.get("strategy", {})
        node_strat = ArchitecturalNode(
            id="node_strategy",
            stage="abstraction",
            title=strat_data.get("title", "Architectural Pattern"),
            subtitle="Design Strategy Applied",
            icon="architecture",
            badge="CORE PATTERN",
            badge_color="cyan",
            status="added",
            details=strat_data.get("details", "Introduces structured coordination to guarantee predictable execution behavior."),
            stats={"category": "Design Strategy"},
        )
        nodes.append(node_strat)

        # Edge: Catalyst -> Strategy
        edges.append(ArchitecturalEdge(
            id="e_cat_strat",
            source="node_catalyst",
            target="node_strategy",
            label="solved by",
            type="triggers",
            animated=True,
        ))

        # Optional second strategy node if an extra grounded concept exists
        last_strat_id = "node_strategy"
        if extra_concept and extra_concept.lower() not in strat_data.get("title", "").lower():
            node_concept = ArchitecturalNode(
                id="node_grounded_concept",
                stage="abstraction",
                title=extra_concept,
                subtitle="Supporting Architectural Concept",
                icon="layers",
                badge="CONCEPT",
                badge_color="lime",
                status="added",
                details=extra_concept_desc or f"Supporting {extra_concept} pattern utilized in this implementation.",
            )
            nodes.append(node_concept)
            edges.append(ArchitecturalEdge(
                id="e_strat_concept",
                source="node_strategy",
                target="node_grounded_concept",
                label="incorporates",
                type="centralizes",
                animated=True,
            ))
            last_strat_id = "node_grounded_concept"

        # ── Node 3: Capabilities Impacted (Stage 3) ──
        cap_data = mind_map.get("capability", {})
        node_cap = ArchitecturalNode(
            id="node_capability",
            stage="service",
            title=cap_data.get("title", "Enhanced Capability"),
            subtitle="User & Business Workflow",
            icon="hub",
            badge="CAPABILITY",
            badge_color="lime",
            status="modified",
            details=cap_data.get("details", "Core workflows updated to adopt the unified execution strategy."),
            stats={"scope": "Operational Feature"},
        )
        nodes.append(node_cap)

        # Edge: Strategy -> Capability
        edges.append(ArchitecturalEdge(
            id="e_strat_cap",
            source=last_strat_id,
            target="node_capability",
            label="empowers",
            type="calls",
            animated=True,
        ))

        # ── Node 4: Watchouts & Gotchas (Stage 4) ──
        watch_data = mind_map.get("watchout", {})
        has_risk = (unattended_count > 0)
        node_watch = ArchitecturalNode(
            id="node_watchout",
            stage="consumer",
            title=watch_data.get("title", "Execution Flow Alignment"),
            subtitle="Developer Attention Point",
            icon="warning" if has_risk else "check_circle",
            badge="ATTENTION REQUIRED" if has_risk else "FLOW ALIGNED",
            badge_color="rose" if has_risk else "slate",
            status="unattended" if has_risk else "handled",
            details=watch_data.get("details", "All touched components appear properly aligned."),
            risk_level="high" if has_risk else "none",
            stats={"risk_status": "Review Required" if has_risk else "Nominal"},
        )
        nodes.append(node_watch)

        # Edge: Capability -> Watchout
        edges.append(ArchitecturalEdge(
            id="e_cap_watch",
            source="node_capability",
            target="node_watchout",
            label="watch for" if has_risk else "verified",
            type="omission_risk" if has_risk else "calls",
            style="alert" if has_risk else "solid",
            animated=has_risk,
        ))

        # ── Node 5: Safety Guarantees (Stage 5) ──
        guar_data = mind_map.get("guarantee", {})
        node_guar = ArchitecturalNode(
            id="node_guarantee",
            stage="safety",
            title=guar_data.get("title", "Integrity Contract"),
            subtitle="System Invariant Verified",
            icon="verified",
            badge="SAFETY CONTRACT",
            badge_color="lime",
            status="safety",
            details=guar_data.get("details", "Guarantees system contract stability and prevents unintended side-effects."),
            stats={"verified": "Pre-deployment Gate"},
        )
        nodes.append(node_guar)

        # Edge: Watchout -> Guarantee
        edges.append(ArchitecturalEdge(
            id="e_watch_guar",
            source="node_watchout",
            target="node_guarantee",
            label="guaranteed by",
            type="verifies",
            style="dashed",
            animated=False,
        ))

        invariants = [guar_data.get("title", "Integrity Contract")]
        narrative = mind_map.get("narrative", "High-level architectural transformation mind map.")

        return ArchitecturalGraphResponse(
            narrative=narrative,
            invariants=invariants,
            blast_level=blast_level,
            unattended_count=unattended_count,
            stages=self.DEFAULT_STAGES,
            nodes=nodes,
            edges=edges,
            source=mind_map.get("source", "mind_map_synthesizer"),
        )


architectural_impact_engine = ArchitecturalImpactEngine()

