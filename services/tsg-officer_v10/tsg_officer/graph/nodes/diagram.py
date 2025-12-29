from __future__ import annotations

from typing import Any, Dict, Literal

from langgraph.types import Command, interrupt

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event
from tsg_officer.tools.llm import LLMClient


def make_diagram_node(llm: LLMClient):
    def diagram(state: TSGState) -> Command[
        Literal["diagram", "review"]
    ]:
        # Step 1: collect description
        desc = state.get("process_description")
        if not desc:
            payload = {
                "type": "process_description",
                "question": "Please describe the process in 3–8 steps (one per line).",
                "hint": "Example: 1) Submit plans 2) TSG reviews 3) Revise 4) Approval",
            }
            answer = interrupt(payload)
            desc = str(answer).strip()

            # UI reasoning summary (safe, user-readable)
            try:
                ui_reasoning = llm.summarize_reasoning(
                    step="diagram_process",
                    question=str(payload.get("question") or "").strip(),
                    answer=desc,
                    context={},
                )
            except Exception:
                ui_reasoning = (
                    "- Captured the process steps you described.\n"
                    "- Next: we'll generate a draft Mermaid flowchart and ask you to confirm it."
                )
            return Command(
                update={
                    "process_description": desc,
                    "ui_reasoning_title": "Diagram reasoning — process description",
                    "ui_reasoning_summary": ui_reasoning,
                    "messages": [{"role": "assistant", "content": "Thanks — generating a draft flowchart now."}],
                    "audit_log": [make_event("process_description_collected", {"preview": desc[:120]})],
                },
                goto="diagram",
            )

        # Step 2: generate mermaid if missing
        mermaid = state.get("flowchart_mermaid")
        if not mermaid:
            flow = llm.generate_flowchart(process_description=desc)
            mermaid = flow.mermaid
            return Command(
                update={
                    "flowchart_mermaid": mermaid,
                    "flowchart_confirmed": False,
                    # capture reasoning summary from the LLM (if available).  For the
                    # OpenAIResponsesLLMClient this explains how the flowchart was
                    # derived from the provided description.
                    "flowchart_reasoning": getattr(llm, "last_reasoning_summary", None),
                    # UI panel (displayed under the user's input area in Streamlit)
                    "ui_reasoning_title": "Diagram reasoning — draft flowchart",
                    "ui_reasoning_summary": (
                        getattr(llm, "last_reasoning_summary", None)
                        or "Generated a draft Mermaid flowchart from your described steps. Please confirm it matches the real process."
                    ),
                    "messages": [
                        {
                            "role": "assistant",
                            "content": (
                                "Draft flowchart (Mermaid) generated. Please review and confirm.\n\n"
                                f"```mermaid\n{mermaid}\n```\n\n"
                                "Reply 'yes' to confirm, or paste corrections."
                            ),
                        }
                    ],
                    "audit_log": [make_event("flowchart_generated", {"chars": len(mermaid)})],
                },
                goto="diagram",
            )

        # Step 3: confirm
        if not bool(state.get("flowchart_confirmed")):
            payload = {
                "type": "flowchart_confirm",
                "question": "Is the flowchart correct? Reply yes / no (or paste the corrected steps).",
                "hint": "If you paste corrected steps, I'll regenerate the Mermaid.",
            }
            answer = interrupt(payload)
            ans = str(answer).strip().lower()

            if ans in ("yes", "y", "correct", "confirmed"):
                # UI reasoning summary
                try:
                    ui_reasoning = llm.summarize_reasoning(
                        step="diagram_confirm",
                        question=str(payload.get("question") or "").strip(),
                        answer=str(answer),
                        context={"confirmed": True},
                    )
                except Exception:
                    ui_reasoning = (
                        "- Flowchart confirmed as accurate.\n"
                        "- Next: moving to the reviewer decision step."
                    )
                return Command(
                    update={
                        "flowchart_confirmed": True,
                        "phase": "REVIEW",
                        "ui_reasoning_title": "Diagram reasoning — confirmation",
                        "ui_reasoning_summary": ui_reasoning,
                        "messages": [{"role": "assistant", "content": "Confirmed. Moving to reviewer decision step."}],
                        "audit_log": [make_event("flowchart_confirmed", {})],
                    },
                    goto="review",
                )

            # If user provided corrections, treat it as new description and regenerate
            new_desc = str(answer).strip()
            # UI reasoning summary (about what we will do with the corrections)
            try:
                ui_reasoning = llm.summarize_reasoning(
                    step="diagram_confirm",
                    question=str(payload.get("question") or "").strip(),
                    answer=new_desc,
                    context={"confirmed": False},
                )
            except Exception:
                ui_reasoning = (
                    "- Received your corrections for the flowchart.\n"
                    "- Next: regenerating the Mermaid diagram from your updated steps."
                )
            flow = llm.generate_flowchart(process_description=new_desc)
            new_mermaid = flow.mermaid
            return Command(
                update={
                    "process_description": new_desc,
                    "flowchart_mermaid": new_mermaid,
                    "flowchart_confirmed": False,
                    "flowchart_reasoning": getattr(llm, "last_reasoning_summary", None),
                    "ui_reasoning_title": "Diagram reasoning — corrections",
                    "ui_reasoning_summary": ui_reasoning,
                    "messages": [
                        {
                            "role": "assistant",
                            "content": (
                                "Updated draft generated. Please confirm:\n\n"
                                f"```mermaid\n{new_mermaid}\n```\n\n"
                                "Reply 'yes' to confirm, or paste more corrections."
                            ),
                        }
                    ],
                    "audit_log": [make_event("flowchart_regenerated", {"chars": len(new_mermaid)})],
                },
                goto="diagram",
            )

        # Already confirmed
        return Command(update={"phase": "REVIEW"}, goto="review")

    return diagram
