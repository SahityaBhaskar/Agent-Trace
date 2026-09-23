from typing import Optional, Literal, Dict, Any
from pydantic import BaseModel, Field

EpistemicStatus = Literal['OBSERVED', 'DECLARED', 'INFERRED']

ActionType = Literal[
    'SESSION_STARTED',
    'USER_REQUEST',
    'FILE_READ',
    'SYMBOL_SEARCH',
    'REPOSITORY_SEARCH',
    'COMMAND_EXECUTED',
    'TEST_EXECUTED',
    'FILE_MODIFIED',
    'FILE_CREATED',
    'FILE_DELETED',
    'BUILD_EXECUTED',
    'AGENT_REASONING_NOTE',
    'SESSION_COMPLETED',
]

class EventTarget(BaseModel):
    file_path: Optional[str] = None
    symbol_name: Optional[str] = None
    query: Optional[str] = None
    command: Optional[str] = None

class EventPayload(BaseModel):
    output_summary: Optional[str] = None
    exit_code: Optional[int] = None
    match_count: Optional[int] = None
    diff_snippet: Optional[str] = None

class SessionEvent(BaseModel):
    event_id: str
    session_id: str
    sequence_number: int
    timestamp: str
    agent_id: str = "claude-code"
    action_type: ActionType
    epistemic_status: EpistemicStatus
    target: Optional[EventTarget] = None
    payload: Optional[EventPayload] = None
    parent_event_id: Optional[str] = None
