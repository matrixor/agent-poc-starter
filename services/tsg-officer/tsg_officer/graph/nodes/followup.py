from __future__ import annotations

import re
from typing import Any, Dict, Literal, Optional

from langgraph.types import Command, interrupt

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event
from tsg_officer.tools.clarifications import (
    BYPASSED_ANSWER_VALUE,
    MAX_EXPLANATION_REQUESTS_PER_QUESTION,
    bump_counter,
    looks_like_clarification_request,
)
from tsg_officer.tools.llm import LLMClient


def make_followup_node(llm: LLMClient):
    """Human-in-the-loop follow-up Q&A.

    The checklist phase can produce follow-up questions for UNKNOWN items.
    This node asks the user for answers, stores them in state, and then
    routes back to the checklist for a re-run.

    We also generate a short LLM 'reasoning summary' after each answer so the
    UI can display a readable explanation under the input area.
    """

    _DIAGRAM_RE = re.compile(
        r"\b(diagram|flow\s*chart|flowchart|architecture\s*diagram|sequence\s*diagram|process\s*diagram|mermaid|visio|draw\.?io|drawio)\b",
        re.I,
    )

    def _is_diagram_request(question: str) -> bool:
        q = (question or "").strip()
        if not q:
            return False
        # Handle explicit PRINCIPLE id mentions and general diagram requests.
        if "principle-diagram" in q.lower():
            return True
        return bool(_DIAGRAM_RE.search(q))

    def _diagram_answer_from_state(state: TSGState) -> Optional[str]:
        """Build a stable, audit-friendly answer string if a diagram already exists."""
        upload = state.get("diagram_upload") or {}
        if isinstance(upload, dict) and (upload.get("path") or upload.get("name")):
            name = str(upload.get("name") or "diagram").strip()
            path = str(upload.get("path") or "").strip()
            sha = str(upload.get("sha256") or "").strip()
            details = []
            if path:
                details.append(f"path: {path}")
            if sha:
                details.append(f"sha256: {sha}")
            suffix = f" ({'; '.join(details)})" if details else ""
            return f"Diagram uploaded: {name}{suffix}"

        mermaid = str(state.get("flowchart_mermaid") or "").strip()
        if mermaid and bool(state.get("flowchart_confirmed")):
            # Include Mermaid so the checklist LLM can use it as evidence.
            return "Diagram generated and confirmed. Mermaid:\n\n```mermaid\n" + mermaid + "\n```"

        return None

    def followup(state: TSGState) -> Command[Literal["followup", "checklist", "diagram"]]:
        report = state.get("checklist_report") or {}
        followups = report.get("followup_questions", []) or []
        idx = int(state.get("followup_index", 0) or 0)
        answers: Dict[str, Any] = dict(state.get("followup_answers", {}) or {})

        # Skip any questions we already have an answer for (e.g. after loading a session).
        while idx < len(followups) and str(followups[idx]) in answers:
            idx += 1

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

        # --------------------------------------------------------------
        # Diagram follow-up enhancement (PRINCIPLE-DIAGRAM, etc.)
        # --------------------------------------------------------------
        if _is_diagram_request(question):
            existing = _diagram_answer_from_state(state)

            # If we already have diagram evidence, auto-answer and advance.
            if existing:
                answers[question] = existing
                return Command(
                    update={
                        "followup_answers": answers,
                        "followup_index": idx + 1,
                        "ui_reasoning_title": f"Follow-up reasoning — {idx + 1}/{len(followups)}",
                        "ui_reasoning_summary": (
                            "- A diagram was already provided earlier in the workflow.\n"
                            "- I attached a reference to it as evidence and moved to the next question."
                        ),
                        "messages": [{"role": "assistant", "content": "Noted — diagram evidence already on file."}],
                        "audit_log": [
                            make_event(
                                "followup_answer_auto_filled",
                                {"index": idx, "q_preview": question[:80], "kind": "diagram"},
                            )
                        ],
                    },
                    goto="followup",
                )

            # Otherwise, route to the diagram node so the UI can offer:
            # - Upload a file
            # - Generate via Q&A
            return Command(
                update={
                    "phase": "DIAGRAM",
                    "pending_diagram_followup": {"index": idx, "question": question},
                    "messages": [
                        {
                            "role": "assistant",
                            "content": (
                                "This follow-up requires a diagram. "
                                "Next, choose whether to upload an existing diagram file or answer questions so I can generate one for you."
                            ),
                        }
                    ],
                    "audit_log": [make_event("diagram_followup_routed", {"index": idx, "q_preview": question[:80]})],
                },
                goto="diagram",
            )

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

        # --------------------------------------------------------------
        # Clarification handling
        # If the user asks for explanation instead of answering, explain and re-ask.
        # After 3 clarification requests for the same question, bypass it.
        # --------------------------------------------------------------

        if not answer_str:
            return Command(
                update={
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "I didn't catch an answer. Please reply with a short answer (or 'N/A' if it doesn't apply).",
                        }
                    ],
                    "audit_log": [make_event("followup_empty_answer", {"index": idx, "q_preview": question[:80]})],
                },
                goto="followup",
            )

        if looks_like_clarification_request(answer_str):
            key = f"followup::{question.strip()}"
            new_counts, n = bump_counter(state.get("clarification_counts"), key)

            # Too many clarification requests => bypass the question and move on.
            if n > MAX_EXPLANATION_REQUESTS_PER_QUESTION:
                answers[question] = BYPASSED_ANSWER_VALUE
                return Command(
                    update={
                        "clarification_counts": new_counts,
                        "followup_answers": answers,
                        "followup_index": idx + 1,
                        "ui_reasoning_title": "Follow-up — bypassed",
                        "ui_reasoning_summary": (
                            f"- You requested clarification **{n}** times for the same question.\n"
                            "- To keep the workflow moving, we bypassed this question for now and moved on."
                        ),
                        "messages": [
                            {
                                "role": "assistant",
                                "content": (
                                    "I’ve explained this question several times already. "
                                    "To keep moving, I’m going to bypass it for now and continue to the next item. "
                                    "If you later want to revisit it, you can provide additional details during the update cycle before final review."
                                ),
                            }
                        ],
                        "audit_log": [
                            make_event(
                                "followup_bypassed_after_clarifications",
                                {"index": idx, "q_preview": question[:80], "clarify_count": n},
                            )
                        ],
                    },
                    goto="followup",
                )

            # Provide an explanation and re-ask.
            try:
                clarification = llm.clarify_question(
                    question=question,
                    user_request=answer_str,
                    context={"index": idx, "total": len(followups), "step": "followup"},
                )
            except Exception:
                clarification = (
                    "Sure — I can clarify.\n\n"
                    "- This question is asking you to provide specific details/evidence.\n"
                    "- Please answer with 1–3 short bullets, and include concrete mechanisms/controls where possible.\n\n"
                    "I’ll re-ask the question next."
                )

            remaining = MAX_EXPLANATION_REQUESTS_PER_QUESTION - n
            remaining_line = (
                f"(You can ask for clarification {remaining} more time(s) for this question before we bypass it.)"
                if remaining > 0
                else "(If you ask for clarification again on this same question, we will bypass it to keep moving.)"
            )

            return Command(
                update={
                    "clarification_counts": new_counts,
                    "ui_reasoning_title": "Follow-up — clarification",
                    "ui_reasoning_summary": (
                        f"- You asked for clarification instead of answering the question (attempt {n}/{MAX_EXPLANATION_REQUESTS_PER_QUESTION}).\n"
                        "- I provided an explanation and will re-ask the same question next.\n"
                        f"- {remaining_line}"
                    ),
                    "messages": [
                        {
                            "role": "assistant",
                            "content": clarification,
                        }
                    ],
                    "audit_log": [
                        make_event(
                            "followup_clarification_provided",
                            {"index": idx, "q_preview": question[:80], "clarify_count": n},
                        )
                    ],
                },
                goto="followup",
            )

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
