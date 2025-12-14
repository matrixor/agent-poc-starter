from __future__ import annotations

from typing import Any, Dict, Literal

from langgraph.types import Command, interrupt

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event


def followup(state: TSGState) -> Command[
    Literal["followup", "checklist"]
]:
    report = state.get("checklist_report") or {}
    followups = report.get("followup_questions", []) or []
    idx = int(state.get("followup_index", 0) or 0)
    answers: Dict[str, Any] = dict(state.get("followup_answers", {}) or {})

    if not followups or idx >= len(followups):
        # back to checklist re-run
        return Command(
            update={
                "phase": "CHECKLIST",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Thanks — I have the clarifications. Re-running the checklist now.",
                    }
                ],
                "audit_log": [make_event("followups_complete", {"count": len(answers)})],
            },
            goto="checklist",
        )

    question = str(followups[idx])

    payload = {
        "type": "followup_question",
        "index": idx,
        "question": question,
        "hint": "Answer in 1–3 sentences. If not applicable, reply N/A.",
    }
    answer = interrupt(payload)

    if isinstance(answer, str):
        answer_str = answer.strip()
    else:
        answer_str = str(answer).strip()

    answers[question] = answer_str

    return Command(
        update={
            "followup_answers": answers,
            "followup_index": idx + 1,
            "messages": [{"role": "assistant", "content": "Noted. Thanks."}],
            "audit_log": [
                make_event(
                    "followup_answer_collected",
                    {"index": idx, "q_preview": question[:80], "a_preview": answer_str[:80]},
                )
            ],
        },
        goto="followup",
    )
