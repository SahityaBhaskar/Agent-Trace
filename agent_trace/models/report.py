from typing import Optional, Literal, Dict, Any, List
from pydantic import BaseModel, Field
from .session import EpistemicStatus, SessionEvent
from .jev_types import ChangeClassification, JevEvaluationResult
from .graph import CausalGraphData
from .impact_graph import SemanticImpactGraph


class AgentDecision(BaseModel):
    """A single reconstructed decision point in the agent's reasoning chain."""
    step: int
    action: str                       # What the agent did
    reasoning: str = ""               # Why — inferred or declared
    epistemic_status: EpistemicStatus = "INFERRED"


class AgentJourney(BaseModel):
    """
    LLM-synthesized reconstruction of how the agent reasoned from the initial
    user request through to the final code changes.
    Populated by GeminiClient.synthesize_agent_journey() when a transcript is available.
    """
    summary: str = ""                              # 2-3 sentence narrative
    decisions: List[AgentDecision] = Field(default_factory=list)
    enriched: bool = False                         # True when Gemini generated this

class DiffHunk(BaseModel):
    id: str
    file_path: str
    symbol: str
    old_lines: str
    new_lines: str
    line_range: str
    change_type: Literal['MODIFIED', 'ADDED', 'DELETED']
    jev_result: JevEvaluationResult
    evidence_event_ids: List[str] = Field(default_factory=list)

class ReviewChecklistItem(BaseModel):
    item: str
    severity: Literal['CRITICAL', 'WARNING', 'INFO']
    rationale: str

class LogicalChange(BaseModel):
    id: str
    title: str
    category: ChangeClassification
    why_explanation: str
    how_arrived_steps: List[str]
    epistemic_status: EpistemicStatus
    confidence_score: float
    hunks: List[DiffHunk]
    affected_services: List[str]
    blast_radius: Literal['isolated', 'localized', 'service_level', 'system_critical']
    review_checklist: List[ReviewChecklistItem]

class ExecutionFlowStep(BaseModel):
    name: str
    role: str
    type: Literal['client', 'service', 'utility', 'database', 'external']

class FlowDescription(BaseModel):
    description: str
    steps: List[ExecutionFlowStep]

class ExecutionFlowDiff(BaseModel):
    before_flow: FlowDescription
    after_flow: FlowDescription
    mermaid_diagram: str

class DeepDiveItem(BaseModel):
    title: str
    body: str

class ReferenceDoc(BaseModel):
    label: str
    url: str
    excerpt: str = ""

class ConceptLesson(BaseModel):
    """AI-generated, grounded lesson for a single LogicalChange."""
    change_id: str
    category_label: str           # e.g. "Concurrency · Lock Mechanism"
    summary: str                  # 1-sentence grounded summary referencing actual symbols
    why_now: str                  # why this concept matters for THIS specific change
    deep_dive: List[DeepDiveItem] = Field(default_factory=list)
    reference_docs: List[ReferenceDoc] = Field(default_factory=list)

class LearningMeta(BaseModel):
    """Realtime AI-enriched learning metadata attached to every ScenarioData."""
    why_now: str                  # grounded banner text for Active Concept tab
    category: str                 # e.g. "Reliability", "Concurrency", "Security"
    deep_dive: List[DeepDiveItem] = Field(default_factory=list)
    reference_docs: List[ReferenceDoc] = Field(default_factory=list)
    change_lessons: List[ConceptLesson] = Field(default_factory=list)
    enriched: bool = False        # False = fallback/empty, True = Gemini-generated

class GroundedConcept(BaseModel):
    name: str
    headline: str
    what_it_is: str
    how_your_repo_uses_it: str
    code_snippet: str
    pitfalls_to_watch: List[str]
    related_concepts: List[str]
    learning_meta: Optional[LearningMeta] = None

class AttentionItem(BaseModel):
    title: str
    level: Literal['HIGH', 'MEDIUM', 'LOW']
    detail: str
    action_required: str
    jev_attention_probability: float
    llm_summary: Optional[str] = None   # LLM-synthesized human-readable summary
    symbol: Optional[str] = None
    file_path: Optional[str] = None
    hunk_id: Optional[str] = None
    diff_snippet: Optional[str] = None
    graph_context: Optional[Dict[str, Any]] = None
    llm_analysis: Optional[Dict[str, Any]] = None

class SessionStats(BaseModel):
    files_inspected: int
    functions_analyzed: int
    relevant_paths: int
    tests_run: int
    execution_time_seconds: int

class ScenarioData(BaseModel):
    id: str
    title: str
    description: str
    user_prompt: str
    stats: SessionStats
    events: List[SessionEvent]
    logical_changes: List[LogicalChange]
    flow_diff: ExecutionFlowDiff
    causal_graph: CausalGraphData
    grounded_concept: GroundedConcept
    attention_items: List[AttentionItem]
    semantic_impact_graph: Optional[SemanticImpactGraph] = None
    agent_journey: Optional[AgentJourney] = None   # How the agent got here
