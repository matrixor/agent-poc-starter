from __future__ import annotations

from typing import Any, Dict, List, Literal

from langgraph.types import Command

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event
from tsg_officer.tools.documents import concat_documents
from tsg_officer.tools.llm import LLMClient
from tsg_officer.tools.rules import RuleRepository


def make_checklist_node(llm: LLMClient, rules_repo: RuleRepository):
    def checklist(state: TSGState) -> Command[
        Literal["followup", "diagram", "review", "finalize"]
    ]:
        case_id = state.get("case_id", "unknown")
        application_type = state.get("application_type") or state.get("intake", {}).get("application_type") or "tsg_general"

        rules = [r.to_dict() for r in rules_repo.list_rules(application_type)]
        rule_by_id: Dict[str, Dict[str, Any]] = {}
        for r in rules:
            rid = str(r.get("rule_id") or "").strip()
            if rid:
                rule_by_id[rid] = r

        intake = state.get("intake", {}) or {}
        # Base submission text either comes from the intake record or from uploaded documents
        submission_text = str(intake.get("submission_text") or "")
        if not submission_text.strip():
            submission_text = concat_documents(state.get("documents", []) or [])

        # ------------------------------------------------------------------
        # Integrate follow‑up answers into the submission text
        #
        # When the checklist engine asks the user for clarifications via the
        # follow‑up phase, the answers are stored in state["followup_answers"].
        # Previously these answers were never passed back to the LLM when
        # re‑running the checklist, causing the same questions to be asked
        # repeatedly and preventing the workflow from reaching a final decision.
        #
        # To fix this, we append the follow‑up question/answer pairs to the
        # submission text before calling generate_checklist_report().  This
        # provides the LLM with the additional context it needs to consider
        # the user’s clarifications, hopefully resolving UNKNOWN statuses.
        followup_answers: Dict[str, Any] = state.get("followup_answers", {}) or {}
        if followup_answers:
            answer_chunks: List[str] = []
            for q, a in followup_answers.items():
                q_str = str(q).strip()
                a_str = str(a).strip()
                if not q_str and not a_str:
                    continue
                # Prefix the answer with a label so the LLM can treat it as
                # additional evidence.  We include the question text for
                # context since the LLM sees only the raw submission.
                answer_chunks.append(f"Follow‑up Question: {q_str}\nAnswer: {a_str}")
            if answer_chunks:
                # Separate the appended answers from the original submission
                # with blank lines to avoid conflating sentences.  Use two
                # newlines to clearly demarcate the answers section.
                submission_text = (
                    submission_text.strip() + "\n\n" + "\n\n".join(answer_chunks)
                ) if submission_text.strip() else "\n\n".join(answer_chunks)

        report_model = llm.generate_checklist_report(
            case_id=case_id,
            application_type=application_type,
            rules=rules,
            submission_text=submission_text,
        )
        report = report_model.model_dump()

        # Build a compact summary message
        overall = report.get("overall_recommendation", "NEED_INFO")
        checklist_items = report.get("checklist", []) or []
        unknown_n = sum(
            1
            for i in checklist_items
            if str(i.get("status") or "").upper() == "UNKNOWN"
        )
        fail_n = sum(
            1
            for i in checklist_items
            if str(i.get("status") or "").upper() == "FAIL"
        )

        summary_lines = [
            f"Checklist complete. Recommendation: **{overall}**",
            f"- FAIL: {fail_n}",
            f"- UNKNOWN: {unknown_n}",
        ]

        followups = report.get("followup_questions", []) or []

        # Some LLM backends omit followup_questions even when checklist items are UNKNOWN.
        # If that happens, synthesize follow-ups from UNKNOWN BLOCKER/WARN items so we
        # don't jump directly to the reviewer decision step.
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

                if q and q not in synthesized:
                    synthesized.append(q)
                if len(synthesized) >= 8:
                    break

            if synthesized:
                report["followup_questions"] = synthesized
                followups = synthesized
        needs_flowchart = (str(intake.get("needs_flowchart") or "")).lower() in ("yes", "y", "true")
        has_flowchart = bool(state.get("flowchart_mermaid")) and bool(state.get("flowchart_confirmed"))

        # Determine whether to ask followup questions again or proceed directly to review.
        # If there are followups and we have not yet collected answers, ask them.
        # If followups exist but we already have answers from a previous checklist run,
        # avoid re‑asking the same questions; instead proceed to review with the current
        # recommendation.  This prevents infinite loops when the checklist continues to
        # generate followup questions despite having context.
        answered_before = bool(state.get("followup_answers"))
        if followups:
            if answered_before:
                # Skip additional clarification rounds and proceed to reviewer step.
                next_phase = "REVIEW"
                goto = "review"
                summary_lines.append("")
                summary_lines.append("Follow‑up questions remain, but proceeding to reviewer decision step.")
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
                    },
                )
            ],

            # capture the reasoning summary from the LLM (if available).  When using
            # OpenAIResponsesLLMClient this would contain a high‑level explanation of
            # how the checklist recommendation was arrived at.  If not present,
            # fall back to the report summary text.
            "checklist_reasoning": getattr(llm, "last_reasoning_summary", None) or report.get("summary"),
        }

        if goto == "followup":
            updates.update({"followup_index": 0, "followup_answers": {}})

        return Command(update=updates, goto=goto)

    return checklist
