from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence

from tsg_officer.schemas.models import (
    ApplicationTypeModel,
    ChecklistReportModel,
    ChecklistItemModel,
    FlowchartModel,
)
from tsg_officer.state.models import now_iso


class LLMClient(Protocol):
    """Small interface so you can swap providers (OpenAI, Azure, internal LLM, etc.)."""

    def classify_application_type(self, user_text: str) -> ApplicationTypeModel:
        ...

    def generate_checklist_report(
        self,
        *,
        case_id: str,
        application_type: str,
        rules: List[Dict[str, Any]],
        submission_text: str,
    ) -> ChecklistReportModel:
        ...

    def generate_flowchart(self, *, process_description: str) -> FlowchartModel:
        ...


@dataclass
class MockLLMClient:
    """Deterministic placeholder that makes the scaffold runnable without external APIs."""

    def classify_application_type(self, user_text: str) -> ApplicationTypeModel:
        text = (user_text or "").lower()
        if any(k in text for k in ["building", "permit", "plan check", "apn", "bsn"]):
            app = "building_permit"
            conf = 0.6
            rationale = "Detected building/permit keywords."
        else:
            app = "tsg_general"
            conf = 0.55
            rationale = "No strong signal; defaulting to general TSG workflow."
        return ApplicationTypeModel(application_type=app, confidence=conf, rationale=rationale)

    def generate_checklist_report(
        self,
        *,
        case_id: str,
        application_type: str,
        rules: List[Dict[str, Any]],
        submission_text: str,
    ) -> ChecklistReportModel:
        text = (submission_text or "").lower()

        checklist: List[ChecklistItemModel] = []
        blocking_issues: List[str] = []
        followups: List[str] = []

        for rule in rules:
            rule_id = rule.get("rule_id", "")
            title = rule.get("title", "")
            desc = rule.get("description", "")
            severity = rule.get("severity", "INFO")
            keywords = rule.get("keywords", []) or []
            keywords_l = [str(k).lower() for k in keywords]

            hits = [k for k in keywords_l if k and k in text]
            if hits:
                status = "PASS"
                confidence = 0.7
                evidence = [{"source": "submission", "excerpt": f"Found keyword(s): {', '.join(hits[:3])}"}]
                missing: List[str] = []
                rationale = "Basic keyword match (mock). Replace with real evidence extraction."
            else:
                status = "UNKNOWN" if severity in ("BLOCKER", "WARN") else "NA"
                confidence = 0.3 if status == "UNKNOWN" else 0.8
                evidence = []
                missing = ["Provide evidence/statement addressing this requirement."] if status == "UNKNOWN" else []
                rationale = "No evidence detected in submission text (mock)."

            item = ChecklistItemModel(
                rule_id=rule_id,
                title=title,
                description=desc,
                status=status,  # type: ignore[arg-type]
                severity=severity,  # type: ignore[arg-type]
                confidence=confidence,
                evidence=evidence,
                missing=missing,
                rationale=rationale,
            )
            checklist.append(item)

            if severity == "BLOCKER" and status == "FAIL":
                blocking_issues.append(f"{rule_id}: {title}")
            if status == "UNKNOWN":
                followups.append(f"Please provide info/evidence for: {title}")

        # overall recommendation
        if any(i.severity == "BLOCKER" and i.status == "FAIL" for i in checklist):
            overall = "REJECT"
        elif any(i.status == "UNKNOWN" for i in checklist):
            overall = "NEED_INFO"
        else:
            overall = "APPROVE"

        summary = (
            f"Mock evaluation for {application_type}. Replace MockLLM with real LLM + retrieval for production."
        )

        return ChecklistReportModel(
            schema_version="1.0",
            case_id=case_id,
            application_type=application_type,
            overall_recommendation=overall,  # type: ignore[arg-type]
            summary=summary,
            checklist=checklist,
            blocking_issues=blocking_issues,
            followup_questions=followups,
            generated_at=now_iso(),
        )

    def generate_flowchart(self, *, process_description: str) -> FlowchartModel:
        # A minimal heuristic to turn lines into nodes
        steps = [s.strip() for s in re.split(r"\n|\r|\.|;", process_description or "") if s.strip()]
        if not steps:
            mermaid = "flowchart TD\n  A[Describe the process] --> B[Add steps]\n"
            questions = ["Please describe the process in 3-8 steps (one per line)."]
        else:
            mermaid_lines = ["flowchart TD"]
            for idx, s in enumerate(steps[:10]):
                node = chr(ord('A') + idx)
                mermaid_lines.append(f"  {node}[{s}]")
                if idx > 0:
                    prev = chr(ord('A') + idx - 1)
                    mermaid_lines.append(f"  {prev} --> {node}")
            mermaid = "\n".join(mermaid_lines) + "\n"
            questions = []

        return FlowchartModel(
            mermaid=mermaid,
            title="Process Flow (draft)",
            assumptions=["Auto-generated from text; confirm accuracy."],
            questions=questions,
        )


class OpenAIChatLLMClient:
    """Example LLM backend using LangChain's ChatOpenAI.

    Install extras:
        pip install -e ".[openai]"

    Environment:
        OPENAI_API_KEY=...
        TSG_OPENAI_MODEL=...
    """

    def __init__(self, *, model: str):
        try:
            from langchain_openai import ChatOpenAI  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ImportError(
                "langchain-openai is not installed. Run: pip install -e '.[openai]' "
            ) from e

        self._ChatOpenAI = ChatOpenAI
        self.model_name = model

    def _model(self, temperature: float = 0.0):
        return self._ChatOpenAI(model=self.model_name, temperature=temperature, streaming=False)

    def classify_application_type(self, user_text: str) -> ApplicationTypeModel:
        model = self._model(temperature=0.0).with_structured_output(ApplicationTypeModel)  # type: ignore[attr-defined]
        messages = [
            {
                "role": "system",
                "content": "You classify TSG application type. Return only the structured object.",
            },
            {"role": "user", "content": user_text},
        ]
        return model.invoke(messages)  # type: ignore[return-value]

    def generate_checklist_report(
        self,
        *,
        case_id: str,
        application_type: str,
        rules: List[Dict[str, Any]],
        submission_text: str,
    ) -> ChecklistReportModel:
        model = self._model(temperature=0.0).with_structured_output(ChecklistReportModel)  # type: ignore[attr-defined]
        prompt = (
            "Evaluate the submission against the rules and produce a checklist report.\n\n"
            f"case_id: {case_id}\n"
            f"application_type: {application_type}\n\n"
            "Rules (JSON):\n"
            f"{json.dumps(rules, ensure_ascii=False, indent=2)}\n\n"
            "Submission text:\n"
            f"{submission_text}\n\n"
            "Rules for your output:\n"
            "- If evidence is missing, use status UNKNOWN and fill missing[].\n"
            "- PASS/FAIL should include evidence excerpts.\n"
        )
        messages = [
            {
                "role": "system",
                "content": "You are a compliance officer. Return ONLY structured output.",
            },
            {"role": "user", "content": prompt},
        ]
        return model.invoke(messages)  # type: ignore[return-value]

    def generate_flowchart(self, *, process_description: str) -> FlowchartModel:
        model = self._model(temperature=0.2).with_structured_output(FlowchartModel)  # type: ignore[attr-defined]
        prompt = (
            "Generate Mermaid flowchart TD code from the described process.\n"
            "Return structured flowchart object with mermaid, assumptions, questions."\
        )
        messages = [
            {"role": "system", "content": "Return ONLY structured output."},
            {"role": "user", "content": prompt + "\n\n" + process_description},
        ]
        return model.invoke(messages)  # type: ignore[return-value]
