from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from langgraph.types import Command, interrupt

from tsg_officer.state.models import TSGState
from tsg_officer.tools.audit import make_event
from tsg_officer.tools.llm import LLMClient


def _has_uploaded_diagram(state: TSGState) -> bool:
    upload = state.get("diagram_upload") or {}
    return bool(isinstance(upload, dict) and (upload.get("path") or upload.get("name")))


def _has_confirmed_mermaid(state: TSGState) -> bool:
    return bool(state.get("flowchart_mermaid")) and bool(state.get("flowchart_confirmed"))


def _diagram_complete(state: TSGState) -> bool:
    return _has_uploaded_diagram(state) or _has_confirmed_mermaid(state)


def _diagram_answer_for_followup(state: TSGState) -> str:
    """Build an audit-friendly answer string for a diagram follow-up question."""
    upload = state.get("diagram_upload") or {}
    if isinstance(upload, dict) and (upload.get("path") or upload.get("name")):
        name = str(upload.get("name") or "diagram").strip()
        mime = str(upload.get("mime_type") or "").strip()
        path = str(upload.get("path") or "").strip()
        sha = str(upload.get("sha256") or "").strip()
        size = upload.get("size_bytes")

        details: list[str] = []
        if mime:
            details.append(mime)
        if isinstance(size, int):
            details.append(f"{size} bytes")
        if path:
            details.append(f"path: {path}")
        if sha:
            details.append(f"sha256: {sha}")

        suffix = f" ({'; '.join(details)})" if details else ""
        return f"Diagram uploaded: {name}{suffix}"

    mermaid = str(state.get("flowchart_mermaid") or "").strip()
    if mermaid and bool(state.get("flowchart_confirmed")):
        return "Diagram generated and confirmed. Mermaid:\n\n```mermaid\n" + mermaid + "\n```"

    return "Diagram provided."


def _route_after_diagram(state: TSGState, updates: Dict[str, Any]) -> Command:
    """Return a Command that routes either back to follow-ups or to review."""

    # Clear mode so future diagram requests re-offer the radio selection.
    updates.setdefault("diagram_input_mode", None)

    pending = state.get("pending_diagram_followup")
    if isinstance(pending, dict) and pending.get("question"):
        # This diagram was requested as a checklist follow-up question.
        idx = int(pending.get("index") or 0)
        question = str(pending.get("question") or "").strip()

        answers: Dict[str, Any] = dict(state.get("followup_answers", {}) or {})
        if question:
            answers[question] = _diagram_answer_for_followup({**state, **updates})  # type: ignore[arg-type]

        # Advance follow-up pointer past this diagram question.
        current_idx = int(state.get("followup_index", 0) or 0)
        next_idx = max(current_idx, idx + 1)

        updates.update(
            {
                "phase": "NEED_INFO",
                "followup_answers": answers,
                "followup_index": next_idx,
                "pending_diagram_followup": None,
            }
        )
        return Command(update=updates, goto="followup")

    # Default: diagram was requested as a normal workflow step.
    updates.update({"phase": "REVIEW"})
    return Command(update=updates, goto="review")


def make_diagram_node(llm: LLMClient):
    """Diagram capture node.

    Enhancement:
    - When a diagram is required, the user can choose:
        1) Upload an existing diagram file, or
        2) Answer questions so the system can generate a Mermaid diagram.

    This is implemented as structured interrupts so the UI can render:
    - a radio button for the mode selection
    - a file uploader for the upload path
    """

    def diagram(state: TSGState) -> Command[Literal["diagram", "review", "followup"]]:
        # If we already completed the diagram step (upload or confirmed mermaid), route onward.
        if _diagram_complete(state):
            return _route_after_diagram(state, updates={})

        # --------------------------------------------------------------
        # Step 0: choose diagram input mode
        # --------------------------------------------------------------
        mode = state.get("diagram_input_mode")
        if mode not in ("upload", "generate"):
            payload = {
                "type": "diagram_mode",
                "question": "A diagram is required. How would you like to provide it?",
                "options": [
                    {"value": "upload", "label": "Upload a diagram file"},
                    {
                        "value": "generate",
                        "label": "Answer questions so I can generate the diagram for you",
                    },
                ],
                "hint": "If you upload, PNG/JPG/SVG/PDF/Draw.io are recommended. If you generate, I'll draft a Mermaid diagram for your confirmation.",
            }
            answer = interrupt(payload)

            raw = answer
            if isinstance(raw, dict):
                raw = raw.get("value") or raw.get("mode") or raw.get("answer")
            mode_str = str(raw or "").strip().lower()
            if mode_str in ("1", "upload", "file", "upload_file"):
                mode = "upload"
            elif mode_str in ("2", "generate", "create", "draft"):
                mode = "generate"
            else:
                # Best-effort fallback: anything else defaults to generate.
                mode = "generate"

            return Command(
                update={
                    "diagram_input_mode": mode,
                    "messages": [
                        {
                            "role": "assistant",
                            "content": (
                                "Okay. "
                                + (
                                    "Please upload the diagram file next."
                                    if mode == "upload"
                                    else "Next I'll ask for the process steps so I can draft a Mermaid diagram."
                                )
                            ),
                        }
                    ],
                    "audit_log": [make_event("diagram_mode_selected", {"mode": mode})],
                },
                goto="diagram",
            )

        # --------------------------------------------------------------
        # Upload path
        # --------------------------------------------------------------
        if mode == "upload":
            if not _has_uploaded_diagram(state):
                payload = {
                    "type": "diagram_upload",
                    "question": "Upload your diagram file.",
                    "hint": "Recommended: PNG/JPG/SVG/PDF/Draw.io. The file will be stored and referenced for audit.",
                }
                answer = interrupt(payload)

                meta: Dict[str, Any] = {}
                if isinstance(answer, dict):
                    meta = dict(answer)
                else:
                    # Fallback: at least store something stable.
                    meta = {"name": str(answer), "path": str(answer)}

                updates = {
                    "diagram_upload": meta,
                    "ui_reasoning_title": "Diagram evidence — uploaded file",
                    "ui_reasoning_summary": (
                        "- Stored a reference to your uploaded diagram file as evidence.\n"
                        "- Next: returning to the workflow."
                    ),
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "Thanks — diagram file received and recorded.",
                        }
                    ],
                    "audit_log": [
                        make_event(
                            "diagram_file_uploaded",
                            {
                                "name": str(meta.get("name") or "")[:120],
                                "mime_type": str(meta.get("mime_type") or "")[:120],
                                "size_bytes": meta.get("size_bytes"),
                            },
                        )
                    ],
                }

                return _route_after_diagram(state, updates)

            # Already uploaded.
            return _route_after_diagram(state, updates={})

        # --------------------------------------------------------------
        # Generate path (existing Mermaid flowchart workflow)
        # --------------------------------------------------------------

        # Step 1: collect description
        desc = state.get("process_description")
        if not desc:
            payload = {
                "type": "process_description",
                "question": "Please describe the process in 3–8 steps (one per line).",
                "hint": "Include: actors/systems, decision points, and where AI/LLM/tooling is used.",
            }
            answer = interrupt(payload)
            desc = str(answer).strip()

            # UI reasoning summary (safe, user-readable)
            try:
                ui_reasoning = llm.summarize_reasoning(
                    step="diagram_process",
                    question=str(payload.get("question") or "").strip(),
                    answer=desc,
                    context={},
                )
            except Exception:
                ui_reasoning = (
                    "- Captured the process steps you described.\n"
                    "- Next: we'll generate a draft Mermaid flowchart and ask you to confirm it."
                )
            return Command(
                update={
                    "process_description": desc,
                    "ui_reasoning_title": "Diagram reasoning — process description",
                    "ui_reasoning_summary": ui_reasoning,
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "Thanks — generating a draft flowchart now.",
                        }
                    ],
                    "audit_log": [make_event("process_description_collected", {"preview": desc[:120]})],
                },
                goto="diagram",
            )

        # Step 2: generate mermaid if missing
        mermaid = state.get("flowchart_mermaid")
        if not mermaid:
            flow = llm.generate_flowchart(process_description=desc)
            mermaid = flow.mermaid
            return Command(
                update={
                    "flowchart_mermaid": mermaid,
                    "flowchart_confirmed": False,
                    # capture reasoning summary from the LLM (if available)
                    "flowchart_reasoning": getattr(llm, "last_reasoning_summary", None),
                    "ui_reasoning_title": "Diagram reasoning — draft flowchart",
                    "ui_reasoning_summary": (
                        getattr(llm, "last_reasoning_summary", None)
                        or "Generated a draft Mermaid flowchart from your described steps. Please confirm it matches the real process."
                    ),
                    "messages": [
                        {
                            "role": "assistant",
                            "content": (
                                "Draft flowchart (Mermaid) generated. Please review and confirm.\n\n"
                                f"```mermaid\n{mermaid}\n```\n\n"
                                "Reply 'yes' to confirm, or paste corrections."
                            ),
                        }
                    ],
                    "audit_log": [make_event("flowchart_generated", {"chars": len(mermaid)})],
                },
                goto="diagram",
            )

        # Step 3: confirm
        if not bool(state.get("flowchart_confirmed")):
            payload = {
                "type": "flowchart_confirm",
                "question": "Is the flowchart correct? Reply yes / no (or paste the corrected steps).",
                "hint": "If you paste corrected steps, I'll regenerate the Mermaid.",
            }
            answer = interrupt(payload)
            ans = str(answer).strip().lower()

            if ans in ("yes", "y", "correct", "confirmed"):
                # UI reasoning summary
                try:
                    ui_reasoning = llm.summarize_reasoning(
                        step="diagram_confirm",
                        question=str(payload.get("question") or "").strip(),
                        answer=str(answer),
                        context={"confirmed": True},
                    )
                except Exception:
                    ui_reasoning = (
                        "- Flowchart confirmed as accurate.\n"
                        "- Next: returning to the workflow."
                    )

                updates = {
                    "flowchart_confirmed": True,
                    "ui_reasoning_title": "Diagram reasoning — confirmation",
                    "ui_reasoning_summary": ui_reasoning,
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "Confirmed. Diagram captured.",
                        }
                    ],
                    "audit_log": [make_event("flowchart_confirmed", {})],
                }
                return _route_after_diagram(state, updates)

            # If user provided corrections, treat it as new description and regenerate
            new_desc = str(answer).strip()
            # UI reasoning summary (about what we will do with the corrections)
            try:
                ui_reasoning = llm.summarize_reasoning(
                    step="diagram_confirm",
                    question=str(payload.get("question") or "").strip(),
                    answer=new_desc,
                    context={"confirmed": False},
                )
            except Exception:
                ui_reasoning = (
                    "- Received your corrections for the flowchart.\n"
                    "- Next: regenerating the Mermaid diagram from your updated steps."
                )
            flow = llm.generate_flowchart(process_description=new_desc)
            new_mermaid = flow.mermaid
            return Command(
                update={
                    "process_description": new_desc,
                    "flowchart_mermaid": new_mermaid,
                    "flowchart_confirmed": False,
                    "flowchart_reasoning": getattr(llm, "last_reasoning_summary", None),
                    "ui_reasoning_title": "Diagram reasoning — corrections",
                    "ui_reasoning_summary": ui_reasoning,
                    "messages": [
                        {
                            "role": "assistant",
                            "content": (
                                "Updated draft generated. Please confirm:\n\n"
                                f"```mermaid\n{new_mermaid}\n```\n\n"
                                "Reply 'yes' to confirm, or paste more corrections."
                            ),
                        }
                    ],
                    "audit_log": [make_event("flowchart_regenerated", {"chars": len(new_mermaid)})],
                },
                goto="diagram",
            )

        # Already confirmed
        return _route_after_diagram(state, updates={})

    return diagram
