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


class DiagramFile(TypedDict, total=False):
    """Metadata reference to a user-provided diagram file.

    IMPORTANT: We intentionally do *not* store raw bytes in workflow state.
    The UI saves the upload to disk and only passes/stores this lightweight
    metadata reference for auditability and to avoid bloating the checkpoint DB.
    """

    name: str
    mime_type: str
    path: str
    size_bytes: int
    sha256: str


class PendingDiagramFollowup(TypedDict, total=False):
    """Tracks which follow-up question triggered the diagram workflow."""

    index: int
    question: str


class TSGState(TypedDict, total=False):
    # identity / workflow
    case_id: str
    phase: Phase

    # conversation
    messages: Annotated[List[ChatMessage], add]

    # intake
    application_type: Optional[str]
    # Optional: some projects fit multiple Chubb AI categories.
    application_categories: Optional[List[str]]
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

    # clarification handling
    # Tracks how many times a user has requested explanation for a given question.
    # Keyed by a stable question identifier (e.g., "followup::<question>", "intake::<field>").
    clarification_counts: Dict[str, int]

    # diagram shortcut approach
    process_description: Optional[str]
    flowchart_mermaid: Optional[str]
    flowchart_confirmed: bool

    # diagram evidence (new)
    diagram_input_mode: Optional[Literal["upload", "generate"]]
    diagram_upload: Optional[DiagramFile]
    pending_diagram_followup: Optional[PendingDiagramFollowup]

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

    # A UI-focused reasoning summary for the *most recent* user turn.
    #
    # This is designed for Streamlit: after the user answers an interrupt
    # question (intake/follow-up/diagram/review), the graph can store a short,
    # user-readable explanation here so the UI can render it under the input.
    ui_reasoning_title: Optional[str]
    ui_reasoning_summary: Optional[str]


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
        "clarification_counts": {},
        "process_description": None,
        "flowchart_mermaid": None,
        "flowchart_confirmed": False,

        # diagram evidence
        "diagram_input_mode": None,
        "diagram_upload": None,
        "pending_diagram_followup": None,

        "final_message_sent": False,
        "reviewer_decision": None,
        "audit_log": [],

        # default reasoning summaries are None.  They will be populated by LLM calls when
        # TSG_LLM_PROVIDER is set to `openai` and reasoning summaries are requested.
        "classification_reasoning": None,
        "checklist_reasoning": None,
        "flowchart_reasoning": None,

        # UI reasoning panel
        "ui_reasoning_title": None,
        "ui_reasoning_summary": None,
    }
