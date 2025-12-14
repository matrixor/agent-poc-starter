from __future__ import annotations

from typing import Any, Dict

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event


def finalize(state: TSGState) -> Dict[str, Any]:
    if state.get("final_message_sent"):
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": "This case is already marked DONE. Use 'New Case' to start another approval.",
                }
            ]
        }

    reviewer = state.get("reviewer_decision", "NEED_INFO")
    report = state.get("checklist_report") or {}
    reco = report.get("overall_recommendation", "NEED_INFO")

    msg_lines = [
        "✅ **Case complete**",
        f"- Reviewer decision: **{reviewer}**",
        f"- Checklist recommendation: **{reco}**",
        "",
        "You can export the full JSON checklist report + audit log from the Streamlit sidebar.",
    ]

    return {
        "final_message_sent": True,
        "messages": [{"role": "assistant", "content": "\n".join(msg_lines)}],
        "audit_log": [make_event("finalized", {"reviewer_decision": reviewer, "checklist_reco": reco})],
    }
