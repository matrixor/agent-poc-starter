from __future__ import annotations

import re
from typing import Any, Dict, List, Literal

from langgraph.types import Command

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event
from tsg_officer.tools.documents import concat_documents
from tsg_officer.tools.llm import LLMClient
from tsg_officer.tools.rules import RuleRepository


_KNOWN_CATEGORIES: List[str] = [
    "Consumer of Internal AI",
    "Consumer of External AI",
    "Internal AI Builder",
]


def _extract_categories_from_text(text: str) -> List[str]:
    if not text:
        return []
    hits: List[str] = []
    for cat in _KNOWN_CATEGORIES:
        # Allow spaces/underscores/hyphens and ignore case.
        tokens = [re.escape(t) for t in cat.split()]
        pat = r"\b" + r"[\s_-]+".join(tokens) + r"\b"
        if re.search(pat, text, re.I):
            hits.append(cat)
    # Dedupe
    out: List[str] = []
    for h in hits:
        if h not in out:
            out.append(h)
    return out


def _normalize_categories(state: TSGState) -> List[str]:
    """Collect application categories from state/intake in a tolerant way."""
    intake = state.get("intake", {}) or {}

    cats = state.get("application_categories")
    if isinstance(cats, list) and cats:
        return [str(c).strip() for c in cats if str(c).strip()]

    cats2 = intake.get("application_categories")
    if isinstance(cats2, list) and cats2:
        return [str(c).strip() for c in cats2 if str(c).strip()]

    # Fall back to parsing the single application_type string if it contains multiple.
    at = str(state.get("application_type") or intake.get("application_type") or "").strip()
    return _extract_categories_from_text(at)


def _is_category_followup(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    # Match the exact rule question and common paraphrases.
    return bool(
        re.search(r"\bWhich\s+Chubb\s+AI\s+category\b", q, re.I)
        or re.search(r"\bWhat\s+type\s+of\s+submission\b", q, re.I)
        or re.search(r"\bSubmission\s+category\b", q, re.I)
    )


def make_checklist_node(llm: LLMClient, rules_repo: RuleRepository):
    def checklist(state: TSGState) -> Command[
        Literal["followup", "diagram", "review", "finalize"]
    ]:
        case_id = state.get("case_id", "unknown")
        intake = state.get("intake", {}) or {}

        categories = _normalize_categories(state)

        # application_type is used mainly for labeling in the report.
        # If multiple categories exist, keep them visible in the label.
        application_type_label = (
            ", ".join(categories)
            if categories
            else (state.get("application_type") or intake.get("application_type") or "tsg_general")
        )

        # Rules: if we have multiple categories, take the union of all rules.
        rules: List[Dict[str, Any]] = []
        rule_by_id: Dict[str, Dict[str, Any]] = {}
        if categories:
            seen: Dict[str, Dict[str, Any]] = {}
            for cat in categories:
                for r in rules_repo.list_rules(cat):
                    d = r.to_dict()
                    rid = str(d.get("rule_id") or "").strip()
                    if rid:
                        seen[rid] = d
            rules = list(seen.values())
        else:
            rules = [r.to_dict() for r in rules_repo.list_rules(str(application_type_label))]

        for r in rules:
            rid = str(r.get("rule_id") or "").strip()
            if rid:
                rule_by_id[rid] = r

        # Base submission text either comes from the intake record or from uploaded documents
        submission_text = str(intake.get("submission_text") or "")
        if not submission_text.strip():
            submission_text = concat_documents(state.get("documents", []) or [])

        # If we already classified categories during intake, explicitly state them as evidence.
        # This prevents redundant follow-ups asking the user to restate the category.
        if categories:
            cat_line = f"Chubb AI category: {', '.join(categories)}"
            rationale = str(state.get("classification_reasoning") or "").strip()
            if rationale:
                cat_line += f"\nCategory rationale: {rationale}"
            submission_text = cat_line + "\n\n" + submission_text

        # ------------------------------------------------------------------
        # Integrate follow-up answers into the submission text
        # ------------------------------------------------------------------
        followup_answers: Dict[str, Any] = state.get("followup_answers", {}) or {}
        if followup_answers:
            answer_chunks: List[str] = []
            for q, a in followup_answers.items():
                q_str = str(q).strip()
                a_str = str(a).strip()
                if not q_str and not a_str:
                    continue
                answer_chunks.append(f"Follow-up Question: {q_str}\nAnswer: {a_str}")
            if answer_chunks:
                submission_text = (
                    submission_text.strip() + "\n\n" + "\n\n".join(answer_chunks)
                ) if submission_text.strip() else "\n\n".join(answer_chunks)

        report_model = llm.generate_checklist_report(
            case_id=case_id,
            application_type=str(application_type_label),
            rules=rules,
            submission_text=submission_text,
        )
        report = report_model.model_dump()

        overall = report.get("overall_recommendation", "NEED_INFO")
        checklist_items = report.get("checklist", []) or []
        unknown_n = sum(1 for i in checklist_items if str(i.get("status") or "").upper() == "UNKNOWN")
        fail_n = sum(1 for i in checklist_items if str(i.get("status") or "").upper() == "FAIL")

        followups = report.get("followup_questions", []) or []

        # Filter redundant category follow-up if we already have a category classification.
        if categories and isinstance(followups, list) and followups:
            followups = [q for q in followups if not _is_category_followup(str(q))]
            report["followup_questions"] = followups

        # Some LLM backends omit followup_questions even when checklist items are UNKNOWN.
        # If that happens, synthesize follow-ups from UNKNOWN BLOCKER/WARN items.
        if (not isinstance(followups, list) or not followups) and checklist_items:
            synthesized: List[str] = []
            for item in checklist_items:
                if not isinstance(item, dict):
                    continue

                status = str(item.get("status") or "").upper()
                severity = str(item.get("severity") or "").upper()
                if status != "UNKNOWN":
                    continue
                if severity not in ("BLOCKER", "WARN"):
                    continue

                rid = str(item.get("rule_id") or "").strip()
                meta = rule_by_id.get(rid, {})
                q = str(meta.get("question") or "").strip()
                if not q:
                    title = str(item.get("title") or rid or "this requirement").strip()
                    missing = item.get("missing") or []
                    missing_hint = ""
                    if isinstance(missing, list) and missing:
                        missing_hint = str(missing[0]).strip()
                    if missing_hint:
                        q = f"{title}: {missing_hint}"
                    else:
                        q = f"Please provide the information/evidence needed for: {title}"

                if categories and _is_category_followup(q):
                    continue

                if q and q not in synthesized:
                    synthesized.append(q)
                if len(synthesized) >= 8:
                    break

            if synthesized:
                report["followup_questions"] = synthesized
                followups = synthesized

        answered_before = bool(state.get("followup_answers"))

        if followups and not answered_before:
            header = f"Initial checklist complete. Initial recommendation: **{overall}** (pending clarifications)"
        else:
            header = f"Checklist complete. Recommendation: **{overall}**"

        summary_lines = [
            header,
            f"- FAIL: {fail_n}",
            f"- UNKNOWN: {unknown_n}",
        ]

        needs_flowchart = (str(intake.get("needs_flowchart") or "")).lower() in ("yes", "y", "true")
        has_flowchart = bool(state.get("flowchart_mermaid")) and bool(state.get("flowchart_confirmed"))

        if followups:
            if answered_before:
                next_phase = "REVIEW"
                goto = "review"
                summary_lines.append("")
                summary_lines.append(
                    "Follow-up questions remain. Proceeding to reviewer decision step (you may still update FAIL/UNKNOWN items before the reviewer finalizes)."
                )
            else:
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
                        "rules_path": str(getattr(rules_repo, "path", "")),
                        "application_type": str(application_type_label),
                        "application_categories": categories,
                    },
                )
            ],
            "checklist_reasoning": getattr(llm, "last_reasoning_summary", None) or report.get("summary"),
            "ui_reasoning_title": "Checklist reasoning summary",
            "ui_reasoning_summary": getattr(llm, "last_reasoning_summary", None) or report.get("summary"),
        }

        if goto == "followup":
            updates.update({"followup_index": 0, "followup_answers": {}})

        return Command(update=updates, goto=goto)

    return checklist
