from __future__ import annotations

import re
from typing import Any, Dict, List, Literal

from langgraph.types import Command, interrupt

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event
from tsg_officer.tools.llm import LLMClient


_FIELD_HELP: Dict[str, Dict[str, str]] = {
    "application_type": {
        "q": "What type of submission is this? (e.g., Consumer of Internal AI, Internal AI Builder)",
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


# Canonical application types supported by this app. Keep these aligned with the
# rule library's `applies_to` values.
_APPLICATION_TYPES: List[str] = [
    "Consumer of Internal AI",
    "Consumer of External AI",
    "Internal AI Builder",

    # Legacy/demo types (still supported by the mock/demo rule library)
    "building_permit",
    "tsg_general",
]


def _normalize_application_type(value: str) -> str:
    """Normalize application type input for matching.

    Accepts common variants like:
    - "internal_ai_builder"
    - "Internal-AI-Builder"
    - "consumer of internal ai"
    """
    v = (value or "").strip().strip('"').strip("'")
    v = re.sub(r"\s+", " ", v)
    # unify separators
    v = v.replace("_", " ").replace("-", " ")
    v = re.sub(r"\s+", " ", v)
    return v.casefold()


def _canonical_application_type(value: str) -> str | None:
    norm = _normalize_application_type(value)
    if not norm:
        return None
    for canonical in _APPLICATION_TYPES:
        if _normalize_application_type(canonical) == norm:
            return canonical
    return None


def _application_type_pattern(canonical: str) -> str:
    """Regex that matches a canonical type allowing spaces/underscores/hyphens."""
    # Example: "Consumer of Internal AI" -> r"consumer[ _-]+of[ _-]+internal[ _-]+ai"
    tokens = [re.escape(t) for t in canonical.split()]
    return r"\b" + r"[\s_-]+".join(tokens) + r"\b"


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

    # Allow application type values with spaces, underscores, or hyphens.
    # Examples:
    # - application type: Consumer of Internal AI
    # - application_type=internal_ai_builder
    m = re.search(r"application[_\s-]*type\s*[:=]\s*(.+)", text, re.I)
    if m:
        raw = (m.group(1) or "").strip()
        # take only the first line if user pasted a paragraph
        raw = raw.splitlines()[0].strip().rstrip(".,;")
        found["application_type"] = _canonical_application_type(raw) or raw

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

    # Allow shorthand application type mention without a prefix.  If the text
    # includes a known application type (e.g. "building_permit" or "tsg_general"),
    # treat it as an explicit application_type override.  This helps users who
    # reply with "It's for building_permit." without the "application type:" prefix.
    for canonical in _APPLICATION_TYPES:
        # Match allowing flexible separators/casing.
        if re.search(_application_type_pattern(canonical), text, re.I):
            found["application_type"] = canonical
            break

    # If we captured an application_type but it's not canonical, try to canonicalize.
    if "application_type" in found:
        found["application_type"] = _canonical_application_type(str(found["application_type"])) or found["application_type"]

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
        classification_reason = None
        if not application_type and last_user.strip():
            guess = llm.classify_application_type(last_user)
            # We accept the guess and keep moving; user can override later.
            application_type = guess.application_type
            intake_data["application_type"] = application_type
            # Capture the rationale if present on the guess (pydantic model)
            try:
                classification_reason = getattr(guess, "rationale", None)
            except Exception:
                classification_reason = None

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
        # Base updates that always get applied before returning.  Include the
        # LLM classification reasoning (if available) to aid debugging.  When
        # using the OpenAIResponsesLLMClient or a model with a rationale field this
        # will contain a natural language explanation of why the application type
        # was chosen.
        update_base = {
            "application_type": application_type,
            "intake": intake_data,
            "required_fields": required_fields,
            "missing_fields": missing_fields,
            "phase": next_phase,
            # Use the classification_reason captured above if available; otherwise fall
            # back to the last_reasoning_summary attribute on the llm (for older
            # implementations that captured reasoning summaries).  This ensures
            # that at least some rationale is stored when provided by the LLM.
            "classification_reasoning": classification_reason or getattr(llm, "last_reasoning_summary", None),
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
