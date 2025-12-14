from __future__ import annotations

from typing import Any, Literal

from langgraph.types import Command, interrupt

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event


_ALLOWED = {
    "approve": "APPROVE",
    "approved": "APPROVE",
    "conditional_approve": "CONDITIONAL_APPROVE",
    "conditional": "CONDITIONAL_APPROVE",
    "cond": "CONDITIONAL_APPROVE",
    "reject": "REJECT",
    "rejected": "REJECT",
    "need_info": "NEED_INFO",
    "need more info": "NEED_INFO",
    "info": "NEED_INFO",
}


def review(state: TSGState) -> Command[
    Literal["finalize", "review"]
]:
    if state.get("reviewer_decision"):
        return Command(update={"phase": "DONE"}, goto="finalize")

    payload = {
        "type": "review_decision",
        "question": "Reviewer decision? (APPROVE / CONDITIONAL_APPROVE / REJECT / NEED_INFO)",
        "hint": "In production this would come from a separate reviewer role; here it's simulated in-chat.",
    }
    answer = interrupt(payload)
    raw = str(answer).strip()
    key = raw.lower().replace("-", "_").replace(" ", "_")
    decision = _ALLOWED.get(key)
    if decision is None and key.upper() in ("APPROVE", "CONDITIONAL_APPROVE", "REJECT", "NEED_INFO"):
        decision = key.upper()
    if decision is None:
        decision = "NEED_INFO"

    return Command(
        update={
            "reviewer_decision": decision,
            "phase": "DONE",
            "messages": [{"role": "assistant", "content": f"Reviewer decision recorded: **{decision}**"}],
            "audit_log": [make_event("reviewer_decision", {"decision": decision, "raw": raw})],
        },
        goto="finalize",
    )
