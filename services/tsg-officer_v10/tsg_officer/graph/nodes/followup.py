from __future__ import annotations

from typing import Any, Dict, Literal

from langgraph.types import Command, interrupt

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event
from tsg_officer.tools.llm import LLMClient


def make_followup_node(llm: LLMClient):
    """Human-in-the-loop follow-up Q&A.

    The checklist phase can produce follow-up questions for UNKNOWN items.
    This node asks the user for answers, stores them in state, and then
    routes back to the checklist for a re-run.

    We also generate a short LLM 'reasoning summary' after each answer so the
    UI can display a readable explanation under the input area.
    """

    def followup(state: TSGState) -> Command[Literal["followup", "checklist"]]:
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

        # UI reasoning summary
        try:
            ui_reasoning = llm.summarize_reasoning(
                step="followup",
                question=question,
                answer=answer_str,
                context={"index": idx, "total": len(followups)},
            )
        except Exception:
            ui_reasoning = (
                "- Captured your clarification for an item that was previously marked UNKNOWN.\n"
                "- Next: we'll append this answer to the submission evidence and re-run the checklist."
            )

        return Command(
            update={
                "followup_answers": answers,
                "followup_index": idx + 1,
                "ui_reasoning_title": f"Follow-up reasoning — {idx + 1}/{len(followups)}",
                "ui_reasoning_summary": ui_reasoning,
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

    return followup
