from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from langgraph.types import Command, interrupt

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event
from tsg_officer.tools.llm import LLMClient


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


# Special resume token used by the UI (non-reviewer) to request an update cycle.
#
# This allows a normal submitter to revisit FAIL/UNKNOWN follow-up questions
# *before* a reviewer finalizes the decision.
UPDATE_ANSWERS_TOKEN = "__TSG_UPDATE_FAIL_UNKNOWN__"


def _format_evidence_line(ev: Dict[str, Any]) -> str:
    source = str(ev.get("source") or ev.get("doc_id") or "").strip()
    page = ev.get("page")
    excerpt = str(ev.get("excerpt") or "").strip()
    parts: List[str] = []
    if source:
        if isinstance(page, int):
            parts.append(f"{source} p.{page}")
        else:
            parts.append(source)
    if excerpt:
        parts.append(f'"{excerpt}"')
    return " — ".join(parts).strip(" —")


def _ai_recommendation_block(state: TSGState) -> str:
    report = state.get("checklist_report") or {}
    if not isinstance(report, dict) or not report:
        return "AI suggested decision: **NEED_INFO**\n\nReasoning: No checklist report was generated."

    overall = str(report.get("overall_recommendation") or "NEED_INFO").strip() or "NEED_INFO"
    summary = str(report.get("summary") or "").strip()
    blocking_issues = report.get("blocking_issues") or []
    checklist_items = report.get("checklist") or []

    lines: List[str] = [f"AI suggested decision: **{overall}**"]

    if summary:
        lines.append("")
        lines.append("Reasoning:")
        lines.append(f"- {summary}")

    if isinstance(blocking_issues, list) and blocking_issues:
        lines.append("")
        lines.append("Key issues:")
        for issue in [str(x).strip() for x in blocking_issues if str(x).strip()][:5]:
            lines.append(f"- {issue}")

    # Evidence highlights from failed/unknown items (prefer BLOCKER/WARN)
    evidence_lines: List[str] = []
    if isinstance(checklist_items, list):
        ranked: List[Dict[str, Any]] = []
        for item in checklist_items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").upper()
            severity = str(item.get("severity") or "").upper()
            if status not in ("FAIL", "UNKNOWN"):
                continue
            sev_rank = {"BLOCKER": 0, "WARN": 1, "INFO": 2}.get(severity, 3)
            ranked.append({"sev_rank": sev_rank, "item": item})
        ranked.sort(key=lambda x: x["sev_rank"])

        for row in ranked[:3]:
            item = row["item"]
            title = str(item.get("title") or item.get("rule_id") or "").strip() or "Checklist item"
            rule_id = str(item.get("rule_id") or "").strip()
            severity = str(item.get("severity") or "").upper()
            rationale = str(item.get("rationale") or "").strip()
            head = f"- {severity}: {title}"
            if rule_id:
                head += f" ({rule_id})"
            if rationale:
                head += f" — {rationale}"
            evidence_lines.append(head)

            evs = item.get("evidence") or []
            if isinstance(evs, list) and evs:
                ev_line = _format_evidence_line(evs[0] if isinstance(evs[0], dict) else {})
                if ev_line:
                    evidence_lines.append(f"  - Evidence: {ev_line}")

    if evidence_lines:
        lines.append("")
        lines.append("Evidence (highlights):")
        lines.extend(evidence_lines)

    # Fallback if report had neither summary nor issues
    if len(lines) == 1:
        lines.append("")
        lines.append("Reasoning: Checklist report did not include a summary or issues list.")

    return "\n".join(lines)


def _dedupe_keep_order(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in items:
        key = x.strip()
        if not key or key in seen:
            continue
        out.append(key)
        seen.add(key)
    return out


def _synthesize_update_questions(state: TSGState) -> List[str]:
    """Build a best-effort list of questions for remaining FAIL/UNKNOWN items.

    Prefer the LLM-provided follow-up questions if available. If they are
    missing, synthesize questions from the checklist items themselves.

    NOTE: Question strings are used as keys in followup_answers; we keep them
    stable and user-readable.
    """

    report = state.get("checklist_report") or {}
    if not isinstance(report, dict) or not report:
        return []

    followups_raw = report.get("followup_questions") or []
    followups: List[str] = []
    if isinstance(followups_raw, list):
        followups = [str(q).strip() for q in followups_raw if str(q).strip()]

    checklist_items = report.get("checklist") or []

    # If the report already provided questions, they are usually the best
    # representation of what the LLM needs clarified.
    questions: List[str] = list(followups)

    # Add synthesized questions for any FAIL/UNKNOWN checklist items that are
    # not clearly covered by the existing follow-up list.
    if isinstance(checklist_items, list) and checklist_items:
        # Rank by severity (BLOCKER first) to keep the experience focused.
        sev_rank_map = {"BLOCKER": 0, "WARN": 1, "INFO": 2}
        ranked: List[Dict[str, Any]] = []
        for item in checklist_items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").upper()
            if status not in ("FAIL", "UNKNOWN"):
                continue
            severity = str(item.get("severity") or "").upper()
            ranked.append({
                "rank": sev_rank_map.get(severity, 3),
                "item": item,
            })
        ranked.sort(key=lambda r: int(r.get("rank", 99)))

        existing_text = "\n".join(questions).lower()
        for row in ranked:
            item = row.get("item") or {}
            rule_id = str(item.get("rule_id") or "").strip()
            title = str(item.get("title") or rule_id or "this requirement").strip()
            severity = str(item.get("severity") or "").upper().strip() or "INFO"
            missing = item.get("missing") or []
            missing_bits: List[str] = []
            if isinstance(missing, list):
                for m in missing:
                    m_str = str(m).strip()
                    if m_str:
                        missing_bits.append(m_str)

            # Skip if we already have an existing follow-up that references the rule id.
            if rule_id and rule_id.lower() in existing_text:
                continue

            # Compose a clear question.
            head = f"{rule_id} ({severity}) — {title}" if rule_id else f"({severity}) — {title}"
            if missing_bits:
                hint = "; ".join(missing_bits[:3])
                q = f"{head}: Please provide/update: {hint}"
            else:
                q = f"{head}: Please provide additional details/evidence to address this requirement."
            questions.append(q)

    return _dedupe_keep_order(questions)


def make_review_node(llm: LLMClient):
    """Reviewer decision step.

    This node is human-in-the-loop, but we still generate a short, user
    readable reasoning summary after the user provides the reviewer decision
    so the UI can display it under the input area.
    """

    def review(state: TSGState) -> Command[Literal["finalize", "review", "followup"]]:
        if state.get("reviewer_decision"):
            return Command(update={"phase": "DONE"}, goto="finalize")

        ai_block = _ai_recommendation_block(state)
        payload = {
            "type": "review_decision",
            "question": (
                f"{ai_block}\n\n"
                "Reviewer decision? (APPROVE / CONDITIONAL_APPROVE / REJECT / NEED_INFO)"
            ),
            "hint": "In production this would come from a separate reviewer role; here it's simulated in-chat.",
        }
        answer = interrupt(payload)
        raw = str(answer).strip()

        # --------------------------------------------------------------
        # Optional: user-requested update cycle before reviewer approval
        # --------------------------------------------------------------
        if raw == UPDATE_ANSWERS_TOKEN:
            # Re-ask the remaining FAIL/UNKNOWN questions so the submitter can
            # improve the evidence before a reviewer finalizes a decision.
            questions = _synthesize_update_questions(state)

            report = state.get("checklist_report") or {}
            if isinstance(report, dict):
                report["followup_questions"] = questions

            # Count the current FAIL/UNKNOWN for context/audit.
            fail_n = 0
            unknown_n = 0
            items = (report.get("checklist") if isinstance(report, dict) else None) or []
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    s = str(it.get("status") or "").upper()
                    if s == "FAIL":
                        fail_n += 1
                    elif s == "UNKNOWN":
                        unknown_n += 1

            return Command(
                update={
                    "phase": "NEED_INFO",
                    "checklist_report": report if isinstance(report, dict) else state.get("checklist_report"),
                    "followup_index": 0,
                    "ui_reasoning_title": "Update answers",
                    "ui_reasoning_summary": (
                        "- You chose to update answers for remaining FAIL/UNKNOWN checklist items.\n"
                        "- Next: I'll re-ask the relevant questions and then re-run the checklist."  
                    ),
                    "messages": [
                        {
                            "role": "assistant",
                            "content": (
                                "Okay — let's update the remaining FAIL/UNKNOWN items before reviewer approval. "
                                "I'll ask the relevant questions again now."
                            ),
                        }
                    ],
                    "audit_log": [
                        make_event(
                            "user_requested_updates",
                            {
                                "fail": fail_n,
                                "unknown": unknown_n,
                                "questions": len(questions),
                            },
                        )
                    ],
                },
                goto="followup",
            )
        key = raw.lower().replace("-", "_").replace(" ", "_")
        decision = _ALLOWED.get(key)
        if decision is None and key.upper() in ("APPROVE", "CONDITIONAL_APPROVE", "REJECT", "NEED_INFO"):
            decision = key.upper()
        if decision is None:
            decision = "NEED_INFO"

        # UI reasoning summary
        ui_reasoning: Optional[str]
        try:
            ui_reasoning = llm.summarize_reasoning(
                step="review_decision",
                question="Reviewer decision",
                answer=raw,
                context={"decision": decision},
            )
        except Exception:
            ui_reasoning = (
                f"- Reviewer decision recorded: **{decision}**.\n"
                "- Next: the case will be finalized and an audit export will be available."
            )

        return Command(
            update={
                "reviewer_decision": decision,
                "phase": "DONE",
                "ui_reasoning_title": "Reviewer decision reasoning",
                "ui_reasoning_summary": ui_reasoning,
                "messages": [{"role": "assistant", "content": f"Reviewer decision recorded: **{decision}**"}],
                "audit_log": [make_event("reviewer_decision", {"decision": decision, "raw": raw})],
            },
            goto="finalize",
        )

    return review
