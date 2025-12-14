from __future__ import annotations

import re
from typing import Any, Dict, List, Literal

from langgraph.types import Command, interrupt

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event
from tsg_officer.tools.llm import LLMClient


_FIELD_HELP: Dict[str, Dict[str, str]] = {
    "application_type": {
        "q": "What type of submission is this? (e.g., building_permit, tsg_general)",
        "hint": "If you're unsure, describe the request and I'll classify it.",
    },
    "project_address": {
        "q": "What is the project address?",
        "hint": "Street address + city is enough for intake.",
    },
    "apn": {
        "q": "What is the APN (Assessor's Parcel Number)?",
        "hint": "Example format: 123-456-789.",
    },
    "bsn": {
        "q": "What is the BSN (Building/Business Serial Number, if applicable)?",
        "hint": "If you don't have it, reply 'N/A'.",
    },
    "scope_summary": {
        "q": "Please summarize the scope in 2–5 bullet points.",
        "hint": "Focus on what is being built/changed and the key systems involved.",
    },
    "submission_text": {
        "q": "Please paste the main submission text (or a short excerpt).",
        "hint": "You can paste directly into chat. For large docs, use the sidebar upload.",
    },
    "needs_flowchart": {
        "q": "Do we need a process/flow diagram for this approval? (yes/no)",
        "hint": "If unsure, answer 'maybe' and we'll decide after checklist evaluation.",
    },
}


def _last_user_text(state: TSGState) -> str:
    msgs = state.get("messages", []) or []
    for m in reversed(msgs):
        if m.get("role") == "user":
            return m.get("content", "") or ""
    return ""


def _try_parse_fields(text: str) -> Dict[str, Any]:
    """Very lightweight parsing so users can paste structured snippets."""
    found: Dict[str, Any] = {}
    if not text:
        return found

    m = re.search(r"application[_\s-]*type\s*[:=]\s*([A-Za-z0-9_\-]+)", text, re.I)
    if m:
        found["application_type"] = m.group(1).strip()

    m = re.search(r"\bAPN\b\s*[:=]?\s*([0-9\-]+)", text, re.I)
    if m:
        found["apn"] = m.group(1).strip()

    m = re.search(r"\bBSN\b\s*[:=]?\s*([0-9\-]+)", text, re.I)
    if m:
        found["bsn"] = m.group(1).strip()

    # crude yes/no parse
    if re.search(r"needs[_\s-]*flowchart\s*[:=]\s*(yes|no|maybe)", text, re.I):
        v = re.search(r"(yes|no|maybe)", text, re.I).group(1).lower()  # type: ignore[union-attr]
        found["needs_flowchart"] = v

    return found


def _required_fields_for(application_type: str) -> List[str]:
    if application_type == "building_permit":
        return [
            "project_address",
            "apn",
            "bsn",
            "scope_summary",
            "submission_text",
            "needs_flowchart",
        ]
    # default
    return ["scope_summary", "submission_text"]


def make_intake_node(llm: LLMClient):
    def intake(state: TSGState) -> Command[
        Literal["intake", "checklist"]
    ]:
        intake_data = dict(state.get("intake", {}) or {})

        # Attempt to parse the last user message into fields (nice UX)
        last_user = _last_user_text(state)
        parsed = _try_parse_fields(last_user)
        if parsed:
            intake_data.update(parsed)

        # Application type classification (only if missing)
        application_type = state.get("application_type") or intake_data.get("application_type")
        if not application_type and last_user.strip():
            guess = llm.classify_application_type(last_user)
            # We accept the guess and keep moving; user can override later.
            application_type = guess.application_type
            intake_data["application_type"] = application_type

        # Determine required + missing fields
        required_fields = state.get("required_fields") or []
        if not required_fields:
            # if still unknown, ask for it
            if not application_type:
                required_fields = ["application_type"]
            else:
                required_fields = _required_fields_for(application_type)

        missing_fields = [f for f in required_fields if f not in intake_data or intake_data.get(f) in (None, "")]
        next_phase = "INTAKE" if missing_fields else "CHECKLIST"
        # Persist computed fields
        update_base = {
            "application_type": application_type,
            "intake": intake_data,
            "required_fields": required_fields,
            "missing_fields": missing_fields,
            "phase": next_phase,
        }

        if missing_fields:
            field = missing_fields[0]
            meta = _FIELD_HELP.get(field, {"q": f"Please provide: {field}", "hint": ""})
            payload = {
                "type": "intake_question",
                "field": field,
                "question": meta.get("q", ""),
                "hint": meta.get("hint", ""),
            }
            answer = interrupt(payload)  # pauses here until resumed

            # Normalize answer a bit
            if isinstance(answer, str):
                answer_str = answer.strip()
            else:
                answer_str = str(answer).strip()

            # Store answer
            intake_data[field] = answer_str
            missing_fields = [f for f in required_fields if f not in intake_data or intake_data.get(f) in (None, "")]

            next_goto = "intake" if missing_fields else "checklist"
            next_phase = "INTAKE" if missing_fields else "CHECKLIST"

            return Command(
                update={
                    **update_base,
                    "intake": intake_data,
                    "missing_fields": missing_fields,
                    "phase": next_phase,
                    "messages": [
                        {
                            "role": "assistant",
                            "content": f"Got it. ({field} recorded.)",
                        }
                    ],
                    "audit_log": [
                        make_event(
                            "intake_field_collected",
                            {"field": field, "value_preview": answer_str[:80]},
                        )
                    ],
                },
                goto=next_goto,
            )

        # Intake complete -> checklist
        return Command(
            update={
                **update_base,
                "phase": "CHECKLIST",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Thanks — intake looks complete. I'll run the checklist evaluation now.",
                    }
                ],
                "audit_log": [make_event("intake_complete", {"application_type": application_type})],
            },
            goto="checklist",
        )

    return intake
