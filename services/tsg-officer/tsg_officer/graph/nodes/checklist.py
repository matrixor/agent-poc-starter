from __future__ import annotations

from typing import Any, Dict, List, Literal

from langgraph.types import Command

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event
from tsg_officer.tools.documents import concat_documents
from tsg_officer.tools.llm import LLMClient
from tsg_officer.tools.rules import RuleRepository


def make_checklist_node(llm: LLMClient, rules_repo: RuleRepository):
    def checklist(state: TSGState) -> Command[
        Literal["followup", "diagram", "review", "finalize"]
    ]:
        case_id = state.get("case_id", "unknown")
        application_type = state.get("application_type") or state.get("intake", {}).get("application_type") or "tsg_general"

        rules = [r.to_dict() for r in rules_repo.list_rules(application_type)]

        intake = state.get("intake", {}) or {}
        submission_text = str(intake.get("submission_text") or "")
        if not submission_text.strip():
            submission_text = concat_documents(state.get("documents", []) or [])

        report_model = llm.generate_checklist_report(
            case_id=case_id,
            application_type=application_type,
            rules=rules,
            submission_text=submission_text,
        )
        report = report_model.model_dump()

        # Build a compact summary message
        overall = report.get("overall_recommendation", "NEED_INFO")
        checklist_items = report.get("checklist", []) or []
        unknown_n = sum(1 for i in checklist_items if i.get("status") == "UNKNOWN")
        fail_n = sum(1 for i in checklist_items if i.get("status") == "FAIL")

        summary_lines = [
            f"Checklist complete. Recommendation: **{overall}**",
            f"- FAIL: {fail_n}",
            f"- UNKNOWN: {unknown_n}",
        ]

        followups = report.get("followup_questions", []) or []
        needs_flowchart = (str(intake.get("needs_flowchart") or "")).lower() in ("yes", "y", "true")
        has_flowchart = bool(state.get("flowchart_mermaid")) and bool(state.get("flowchart_confirmed"))

        if followups:
            next_phase = "NEED_INFO"
            goto = "followup"
            summary_lines.append("")
            summary_lines.append("I still need a few clarifications before we can approve:")
            for q in followups[:5]:
                summary_lines.append(f"- {q}")
        elif needs_flowchart and not has_flowchart:
            next_phase = "DIAGRAM"
            goto = "diagram"
            summary_lines.append("")
            summary_lines.append("Next: let's generate/confirm the required flow diagram.")
        else:
            next_phase = "REVIEW"
            goto = "review"
            summary_lines.append("")
            summary_lines.append("Next: reviewer decision step.")

        updates: Dict[str, Any] = {
            "phase": next_phase,
            "checklist_report": report,
            "messages": [{"role": "assistant", "content": "\n".join(summary_lines)}],
            "audit_log": [
                make_event(
                    "checklist_generated",
                    {
                        "overall": overall,
                        "fail": fail_n,
                        "unknown": unknown_n,
                        "rules_count": len(rules),
                    },
                )
            ],
        }

        if goto == "followup":
            updates.update({"followup_index": 0, "followup_answers": {}})

        return Command(update=updates, goto=goto)

    return checklist
