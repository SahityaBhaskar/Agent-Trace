from typing import Optional, Literal, Dict, Any, List
from pydantic import BaseModel, Field

ChangeClassification = Literal[
    'DIRECT_REQUIREMENT',
    'SUPPORTING_CHANGE',
    'REFACTOR',
    'COMMONIZATION',
    'DEPENDENCY_CHANGE',
    'API_CHANGE',
    'DATA_MODEL_CHANGE',
    'CONFIGURATION_CHANGE',
    'TEST_CHANGE',
    'GENERATED_CHANGE',
    'BEHAVIORAL_CHANGE',
    'SECURITY_RELEVANT_CHANGE',
    'PERFORMANCE_RELEVANT_CHANGE'
]

class JevClassificationResult(BaseModel):
    category: ChangeClassification
    confidence: float
    choice: str

class JevBlastRadiusResult(BaseModel):
    score: float
    level: Literal['isolated', 'localized', 'service_level', 'system_critical']

class JevHumanReviewResult(BaseModel):
    required: bool
    probability: float

class JevBreakingRiskResult(BaseModel):
    is_breaking: bool
    probability: float

JevOmissionSeverity = Literal['none', 'low', 'medium_behavioral', 'critical_breaking']

class JevOmissionResult(BaseModel):
    has_omission_risk: bool
    severity: JevOmissionSeverity = "none"
    probability: float = 0.0
    unattended_symbols: List[str] = Field(default_factory=list)
    handled_symbols: List[str] = Field(default_factory=list)
    rationale: str = ""
    action_recommendation: str = ""

class JevEvaluationResult(BaseModel):
    hunk_id: str
    classification: JevClassificationResult
    blast_radius: JevBlastRadiusResult
    human_review: JevHumanReviewResult
    breaking_risk: JevBreakingRiskResult
    latency_ms: int
    source: Literal['live_api', 'calibrated_cache']
    omission: Optional[JevOmissionResult] = None
