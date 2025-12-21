from __future__ import annotations

from typing import Any, Dict, List, Literal

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


def review(state: TSGState) -> Command[
    Literal["finalize", "review"]
]:
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
