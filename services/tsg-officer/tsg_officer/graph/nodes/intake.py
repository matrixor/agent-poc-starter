from __future__ import annotations

import re
from typing import Any, Dict, List, Literal

from langgraph.types import Command, interrupt

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event
from tsg_officer.tools.llm import LLMClient


_FIELD_HELP: Dict[str, Dict[str, str]] = {
    "application_type": {
        "q": "What type of submission is this? (e.g., Consumer of Internal AI, Consumer of External AI, Internal AI Builder)",
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

_AI_CATEGORIES: List[str] = [
    "Consumer of Internal AI",
    "Consumer of External AI",
    "Internal AI Builder",
]


def _normalize_application_type(value: str) -> str:
    """Normalize application type input for matching."""
    v = (value or "").strip().strip('"').strip("'")
    v = re.sub(r"\s+", " ", v)
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
    tokens = [re.escape(t) for t in canonical.split()]
    return r"\b" + r"[\s_-]+".join(tokens) + r"\b"


def _extract_ai_categories(text: str) -> List[str]:
    """Extract one or more Chubb AI categories from free text."""
    if not text:
        return []

    hits: List[str] = []
    for canonical in _AI_CATEGORIES:
        if re.search(_application_type_pattern(canonical), text, re.I):
            hits.append(canonical)

    # Dedupe while keeping order
    deduped: List[str] = []
    for h in hits:
        if h not in deduped:
            deduped.append(h)
    return deduped


def _pick_primary_category(categories: List[str]) -> str:
    """Pick a stable primary category for routing/labels."""
    if not categories:
        return "tsg_general"
    # Prefer Internal AI Builder when present since it typically has the most controls.
    if "Internal AI Builder" in categories:
        return "Internal AI Builder"
    return categories[0]


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

    # application_type values with spaces/underscores/hyphens.
    m = re.search(r"application[_\s-]*type\s*[:=]\s*(.+)", text, re.I)
    if m:
        raw = (m.group(1) or "").strip()
        raw = raw.splitlines()[0].strip().rstrip(".,;")
        cats = _extract_ai_categories(raw)
        if cats:
            found["application_categories"] = cats
            found["application_type"] = ", ".join(cats)
        else:
            found["application_type"] = _canonical_application_type(raw) or raw

    m = re.search(r"\bAPN\b\s*[:=]?\s*([0-9\-]+)", text, re.I)
    if m:
        found["apn"] = m.group(1).strip()

    m = re.search(r"\bBSN\b\s*[:=]?\s*([0-9\-]+)", text, re.I)
    if m:
        found["bsn"] = m.group(1).strip()

    if re.search(r"needs[_\s-]*flowchart\s*[:=]\s*(yes|no|maybe)", text, re.I):
        v = re.search(r"(yes|no|maybe)", text, re.I).group(1).lower()  # type: ignore[union-attr]
        found["needs_flowchart"] = v

    # Shorthand category mention without a prefix.
    cats = _extract_ai_categories(text)
    if cats:
        found["application_categories"] = cats
        found["application_type"] = ", ".join(cats)

    # Canonicalize single type if needed.
    if "application_type" in found and "application_categories" not in found:
        found["application_type"] = _canonical_application_type(str(found["application_type"])) or found["application_type"]

    return found


def _required_fields_for(application_type: str) -> List[str]:
    """Required intake fields.

    UX requirement: start with submission_text, then classify categories.
    """
    if application_type == "building_permit":
        return [
            "project_address",
            "apn",
            "bsn",
            "scope_summary",
            "submission_text",
            "needs_flowchart",
        ]

    # Default for AI governance: we can run an initial checklist from submission_text.
    return ["submission_text"]


def make_intake_node(llm: LLMClient):
    def _classify_from_submission(intake_data: Dict[str, Any]) -> Dict[str, Any]:
        """Classify Chubb AI categories using submission_text (best-effort)."""
        submission_text = str(intake_data.get("submission_text") or "").strip()
        if not submission_text:
            return {"application_type": intake_data.get("application_type"), "application_categories": intake_data.get("application_categories"), "classification_reason": None}

        # If the user already provided categories explicitly, don't override.
        existing_cats = intake_data.get("application_categories") or []
        if isinstance(existing_cats, list) and existing_cats:
            primary = _pick_primary_category(existing_cats)
            return {"application_type": primary, "application_categories": existing_cats, "classification_reason": None}

        guess = llm.classify_application_type(submission_text)
        classification_reason = getattr(guess, "rationale", None)

        guess_text = str(getattr(guess, "application_type", "") or "")
        cats = _extract_ai_categories(guess_text)
        if not cats:
            # As a fallback, see if the submission itself explicitly names categories.
            cats = _extract_ai_categories(submission_text)

        if cats:
            intake_data["application_categories"] = cats
            intake_data["application_type"] = ", ".join(cats)
            primary = _pick_primary_category(cats)
            return {"application_type": primary, "application_categories": cats, "classification_reason": classification_reason}

        # Otherwise accept the single predicted type.
        canonical = _canonical_application_type(guess_text) or guess_text
        intake_data["application_type"] = canonical
        return {"application_type": canonical, "application_categories": [], "classification_reason": classification_reason}

    def intake(state: TSGState) -> Command[Literal["intake", "checklist"]]:
        intake_data = dict(state.get("intake", {}) or {})

        last_user = _last_user_text(state)
        parsed = _try_parse_fields(last_user)
        if parsed:
            intake_data.update(parsed)

        # Classification is driven by submission_text (not the first short answer).
        classification_reason = None
        primary_type = state.get("application_type") or intake_data.get("application_type")
        categories: List[str] = []

        if isinstance(state.get("application_categories"), list):
            categories = list(state.get("application_categories") or [])
        if not categories and isinstance(intake_data.get("application_categories"), list):
            categories = list(intake_data.get("application_categories") or [])

        # If user provided a multi-category string in application_type, parse it.
        if not categories and primary_type:
            categories = _extract_ai_categories(str(primary_type))
            if categories:
                intake_data["application_categories"] = categories
                intake_data["application_type"] = ", ".join(categories)

        if str(intake_data.get("submission_text") or "").strip():
            classified = _classify_from_submission(intake_data)
            classification_reason = classified.get("classification_reason")
            # Prefer categories if found.
            cats2 = classified.get("application_categories") or []
            if isinstance(cats2, list) and cats2:
                categories = cats2
                primary_type = classified.get("application_type") or primary_type
            else:
                primary_type = classified.get("application_type") or primary_type

        # Determine required + missing fields.
        required_fields = state.get("required_fields") or []
        if not required_fields:
            # UX: Always start by collecting submission_text.
            has_submission = bool(str(intake_data.get("submission_text") or "").strip())
            if not has_submission:
                required_fields = ["submission_text"]
            else:
                required_fields = _required_fields_for(primary_type or "tsg_general")

        missing_fields = [
            f for f in required_fields
            if f not in intake_data or intake_data.get(f) in (None, "")
        ]

        next_phase = "INTAKE" if missing_fields else "CHECKLIST"

        update_base: Dict[str, Any] = {
            "application_type": primary_type,
            "application_categories": categories,
            "intake": intake_data,
            "required_fields": required_fields,
            "missing_fields": missing_fields,
            "phase": next_phase,
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
            answer = interrupt(payload)

            answer_str = answer.strip() if isinstance(answer, str) else str(answer).strip()
            intake_data[field] = answer_str

            # If we just got submission_text, classify categories now.
            if field == "submission_text" and answer_str.strip():
                classified = _classify_from_submission(intake_data)
                classification_reason = classified.get("classification_reason")
                cats2 = classified.get("application_categories") or []
                if isinstance(cats2, list) and cats2:
                    categories = cats2
                    primary_type = classified.get("application_type") or primary_type
                else:
                    primary_type = classified.get("application_type") or primary_type

                # Recompute required fields for building_permit if the classifier picked it.
                required_fields = _required_fields_for(primary_type or "tsg_general")

            missing_fields = [
                f for f in required_fields
                if f not in intake_data or intake_data.get(f) in (None, "")
            ]

            try:
                ui_reasoning = llm.summarize_reasoning(
                    step="intake",
                    question=str(meta.get("q") or "").strip(),
                    answer=answer_str,
                    context={
                        "field": field,
                        "classified_categories": categories,
                        "primary_application_type": primary_type,
                        "remaining_fields": missing_fields,
                    },
                )
            except Exception:
                if missing_fields:
                    ui_reasoning = (
                        f"- Recorded **{field}** from your answer.\n"
                        f"- Next: we still need {len(missing_fields)} more intake item(s) before running the checklist."
                    )
                else:
                    ui_reasoning = (
                        f"- Recorded **{field}** from your answer.\n"
                        "- Next: intake is complete, so we'll run the checklist evaluation."
                    )

            next_goto = "intake" if missing_fields else "checklist"
            next_phase = "INTAKE" if missing_fields else "CHECKLIST"

            return Command(
                update={
                    **update_base,
                    "application_type": primary_type,
                    "application_categories": categories,
                    "intake": intake_data,
                    "required_fields": required_fields,
                    "missing_fields": missing_fields,
                    "phase": next_phase,
                    "classification_reasoning": classification_reason or getattr(llm, "last_reasoning_summary", None),
                    "ui_reasoning_title": f"Intake reasoning — {field}",
                    "ui_reasoning_summary": ui_reasoning,
                    "messages": [
                        {"role": "assistant", "content": f"Got it. ({field} recorded.)"}
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
                "audit_log": [
                    make_event(
                        "intake_complete",
                        {
                            "application_type": primary_type,
                            "application_categories": categories,
                        },
                    )
                ],
            },
            goto="checklist",
        )

    return intake
