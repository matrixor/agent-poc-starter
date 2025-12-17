from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from typing_extensions import Annotated, TypedDict
from operator import add
from datetime import datetime, timezone
import uuid


Role = Literal["user", "assistant", "system"]
Phase = Literal[
    "START",
    "INTAKE",
    "CHECKLIST",
    "NEED_INFO",
    "DIAGRAM",
    "REVIEW",
    "DONE",
]

Decision = Literal["APPROVE", "CONDITIONAL_APPROVE", "REJECT", "NEED_INFO"]

ChecklistStatus = Literal["PASS", "FAIL", "NA", "UNKNOWN"]
ChecklistSeverity = Literal["BLOCKER", "WARN", "INFO"]


class ChatMessage(TypedDict):
    role: Role
    content: str


class Evidence(TypedDict, total=False):
    doc_id: str
    source: str          # filename, label, etc
    page: int
    excerpt: str         # short quote/snippet (keep it small for auditability)


class ChecklistItem(TypedDict):
    rule_id: str
    title: str
    description: str
    status: ChecklistStatus
    severity: ChecklistSeverity
    confidence: float
    evidence: List[Evidence]
    missing: List[str]
    rationale: str


class ChecklistReport(TypedDict):
    schema_version: str
    case_id: str
    application_type: str
    overall_recommendation: Decision
    summary: str
    checklist: List[ChecklistItem]
    blocking_issues: List[str]
    followup_questions: List[str]
    generated_at: str  # ISO timestamp


class AuditEvent(TypedDict):
    ts: str  # ISO timestamp
    event: str
    details: Dict[str, Any]


class Document(TypedDict, total=False):
    doc_id: str
    name: str
    mime_type: str
    text: str


class TSGState(TypedDict, total=False):
    # identity / workflow
    case_id: str
    phase: Phase

    # conversation
    messages: Annotated[List[ChatMessage], add]

    # intake
    application_type: Optional[str]
    intake: Dict[str, Any]
    required_fields: List[str]
    missing_fields: List[str]

    # documents
    documents: List[Document]

    # checklist + decision
    checklist_report: Optional[ChecklistReport]

    # follow-ups (from checklist)
    followup_index: int
    followup_answers: Dict[str, Any]

    # diagram shortcut approach
    process_description: Optional[str]
    flowchart_mermaid: Optional[str]
    flowchart_confirmed: bool

    # completion
    final_message_sent: bool

    # review
    reviewer_decision: Optional[Decision]

    # audit trail
    audit_log: Annotated[List[AuditEvent], add]

    # optional reasoning summaries captured from the LLM.  These fields store
    # natural language explanations returned by the LLM (when enabled) for
    # different phases of the workflow.  They allow the web UI to surface
    # "why" a classification or recommendation was made without needing to
    # expose internal chain‑of‑thought.  See the LLM implementation for
    # details on how these are populated.
    classification_reasoning: Optional[str]
    checklist_reasoning: Optional[str]
    flowchart_reasoning: Optional[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_case_state(case_id: Optional[str] = None) -> TSGState:
    cid = case_id or str(uuid.uuid4())
    return {
        "case_id": cid,
        "phase": "START",
        "messages": [],
        "intake": {},
        "required_fields": [],
        "missing_fields": [],
        "documents": [],
        "checklist_report": None,
        "followup_index": 0,
        "followup_answers": {},
        "process_description": None,
        "flowchart_mermaid": None,
        "flowchart_confirmed": False,
        "final_message_sent": False,
        "reviewer_decision": None,
        "audit_log": [],

        # default reasoning summaries are None.  They will be populated by LLM calls when
        # TSG_LLM_PROVIDER is set to `openai` and reasoning summaries are requested.
        "classification_reasoning": None,
        "checklist_reasoning": None,
        "flowchart_reasoning": None,
    }
