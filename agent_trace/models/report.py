from typing import Optional, Literal, Dict, Any, List
from pydantic import BaseModel, Field
from .session import EpistemicStatus, SessionEvent
from .jev_types import ChangeClassification, JevEvaluationResult
from .graph import CausalGraphData

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

class GroundedConcept(BaseModel):
    name: str
    headline: str
    what_it_is: str
    how_your_repo_uses_it: str
    code_snippet: str
    pitfalls_to_watch: List[str]
    related_concepts: List[str]

class AttentionItem(BaseModel):
    title: str
    level: Literal['HIGH', 'MEDIUM', 'LOW']
    detail: str
    action_required: str
    jev_attention_probability: float

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
