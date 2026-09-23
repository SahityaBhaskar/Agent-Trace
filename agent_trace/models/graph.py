from typing import Optional, Literal, Dict, Any, List
from pydantic import BaseModel, Field
from .session import EpistemicStatus

NodeType = Literal[
    'USER_REQUEST',
    'AGENT_INVESTIGATION',
    'ARCHITECTURAL_DECISION',
    'LOGICAL_CHANGE',
    'CODE_HUNK',
    'RISK_FLAG',
    'LEARNING_CONCEPT'
]

EdgeRelation = Literal[
    'MOTIVATED_BY',
    'DISCOVERED_IN',
    'MODIFIES',
    'INTRODUCED',
    'CREATES_RISK',
    'GROUNDED_IN',
    'VALIDATED_BY'
]

class CausalNode(BaseModel):
    id: str
    type: NodeType
    title: str
    subtitle: Optional[str] = None
    epistemic_status: EpistemicStatus
    confidence_score: float = 1.0
    data: Dict[str, Any] = Field(default_factory=dict)

class CausalEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: EdgeRelation
    confidence: float = 1.0
    label: Optional[str] = None

class CausalGraphData(BaseModel):
    nodes: List[CausalNode] = Field(default_factory=list)
    edges: List[CausalEdge] = Field(default_factory=list)
