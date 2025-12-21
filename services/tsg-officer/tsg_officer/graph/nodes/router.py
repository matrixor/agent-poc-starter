from __future__ import annotations

from typing_extensions import Literal

from langgraph.types import Command

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event


def route(state: TSGState) -> Command[
    Literal["intake", "checklist", "followup", "diagram", "review", "finalize"]
]:
    """Central router so each invocation can resume the right phase.

    This keeps Streamlit simple: you can call graph.invoke(...) repeatedly with the same thread_id.
    """
    phase = state.get("phase", "START")

    # START -> greet -> INTAKE
    if phase == "START":
        return Command(
            update={
                "phase": "INTAKE",
                "messages": [
                    {
                        "role": "assistant",
                        "content": (
                            "Hi — I'm TSG Virtual AI Officer. I'll help you complete TSG intake for AI and produce an auditable checklist.\n\n"
                        ),
                    }
                ],
                "audit_log": [make_event("case_started", {"phase": "INTAKE"})],
            },
            goto="intake",
        )

    if phase in ("INTAKE", "NEED_INFO"):
        return Command(goto="intake")

    if phase == "CHECKLIST":
        return Command(goto="checklist")

    if phase == "DIAGRAM":
        return Command(goto="diagram")

    if phase == "REVIEW":
        return Command(goto="review")

    # DONE (or unknown) -> finalize (idempotent)
    return Command(goto="finalize")
