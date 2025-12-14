from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


ChecklistStatus = Literal["PASS", "FAIL", "NA", "UNKNOWN"]
ChecklistSeverity = Literal["BLOCKER", "WARN", "INFO"]
Decision = Literal["APPROVE", "CONDITIONAL_APPROVE", "REJECT", "NEED_INFO"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceModel(BaseModel):
    doc_id: Optional[str] = Field(default=None, description="Internal document identifier.")
    source: Optional[str] = Field(default=None, description="Filename or label for the evidence source.")
    page: Optional[int] = Field(default=None, description="Page number if applicable (1-indexed).")
    excerpt: str = Field(..., description="A short excerpt supporting the evaluation.")


class ChecklistItemModel(BaseModel):
    rule_id: str
    title: str
    description: str

    status: ChecklistStatus
    severity: ChecklistSeverity

    confidence: float = Field(..., ge=0.0, le=1.0)

    evidence: List[EvidenceModel] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)

    rationale: str = Field(..., description="Short explanation for PASS/FAIL/UNKNOWN/NA.")


class ChecklistReportModel(BaseModel):
    schema_version: str = Field(default="1.0", description="Schema version for audit/compatibility.")
    case_id: str
    application_type: str

    overall_recommendation: Decision

    summary: str

    checklist: List[ChecklistItemModel]

    blocking_issues: List[str] = Field(default_factory=list)
    followup_questions: List[str] = Field(default_factory=list)

    generated_at: str = Field(default_factory=now_iso, description="ISO timestamp when report was generated.")


class FlowchartModel(BaseModel):
    mermaid: str = Field(..., description="Mermaid flowchart code (flowchart TD ...).")
    title: Optional[str] = Field(default=None)
    assumptions: List[str] = Field(default_factory=list)
    questions: List[str] = Field(default_factory=list)


class ApplicationTypeModel(BaseModel):
    application_type: str = Field(..., description="High-level application type.")
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = Field(..., description="Why this classification was chosen.")
