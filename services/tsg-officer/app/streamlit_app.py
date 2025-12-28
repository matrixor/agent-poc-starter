from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # loads .env into os.environ


import json
import html
import re
from pathlib import Path
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

import streamlit as st
import streamlit.components.v1 as components

from langgraph.types import Command

from tsg_officer.config import Settings
from tsg_officer.graph import build_graph
from tsg_officer.state import new_case_state


st.set_page_config(page_title="TSG - AI", page_icon="✅", layout="wide")


# Special resume token used to trigger the "update FAIL/UNKNOWN answers" loop.
# This must match tsg_officer.graph.nodes.review.UPDATE_ANSWERS_TOKEN.
UPDATE_ANSWERS_TOKEN = "__TSG_UPDATE_FAIL_UNKNOWN__"


# ---------------------------------------------------------------------------
# Lightweight UI session persistence
#
# Why this exists:
# - LangGraph checkpointing persists *workflow state* (TSGState)
# - Streamlit session_state stores the *UI transcript* (including interrupt
#   questions and the reasoning panels, which are not in graph messages)
#
# To make "Search session history by Case ID" actually work, we save a
# small JSON file per case containing the UI transcript + a few UI flags.
# ---------------------------------------------------------------------------


def _session_store_dir() -> Path:
    """Directory where per-thread UI transcripts are stored."""
    # Keep UI persistence alongside the checkpoint DB so operators can move
    # both together if they override TSG_CHECKPOINT_DB.
    try:
        settings = Settings.from_env()
        db_path = Path(settings.checkpoint_db).expanduser().resolve()
        base = db_path.parent
    except Exception:
        base = Path(__file__).resolve().parent.parent

    d = base / ".tsg_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]")


def _session_file(thread_id: str) -> Path:
    safe = _SAFE_ID_RE.sub("_", (thread_id or "").strip())
    if not safe:
        safe = "unknown"
    return _session_store_dir() / f"{safe}.json"


def _persist_ui_session(*, graph=None) -> None:
    """Persist the current UI transcript + a few UI flags to disk (best-effort)."""

    thread_id = str(st.session_state.get("thread_id") or "").strip()
    if not thread_id:
        return

    payload: Dict[str, Any] = {
        "schema_version": 1,
        "thread_id": thread_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "chat": st.session_state.get("chat", []),
        "awaiting_resume": bool(st.session_state.get("awaiting_resume", False)),
        "graph_messages_len": int(st.session_state.get("graph_messages_len", 0) or 0),
        "initialized": bool(st.session_state.get("initialized", False)),
        "last_interrupt_payload": st.session_state.get("last_interrupt_payload"),
        "ui_reasoning_title": st.session_state.get("ui_reasoning_title"),
        "ui_reasoning_summary": st.session_state.get("ui_reasoning_summary"),
        "user_waiting_for_reviewer": bool(st.session_state.get("user_waiting_for_reviewer", False)),
    }

    # Optional: include a small status summary for dashboards.
    if graph is not None:
        try:
            config = {"configurable": {"thread_id": thread_id}}
            snap = graph.get_state(config)
            payload["phase"] = snap.values.get("phase")
            payload["reviewer_decision"] = snap.values.get("reviewer_decision")
            report = snap.values.get("checklist_report") or {}
            if isinstance(report, dict):
                payload["overall_recommendation"] = report.get("overall_recommendation")
        except Exception:
            pass

    # Atomic write: write to a temp file then replace.
    try:
        path = _session_file(thread_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        # Non-fatal: UI will still work, but history search won't resume.
        return


def _load_ui_session(thread_id: str) -> Dict[str, Any] | None:
    """Load a saved UI transcript for a thread (best-effort)."""
    tid = (thread_id or "").strip()
    if not tid:
        return None
    path = _session_file(tid)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _apply_loaded_session(data: Dict[str, Any], *, graph) -> bool:
    """Apply a loaded session payload into Streamlit session_state."""
    tid = str(data.get("thread_id") or "").strip()
    if not tid:
        return False

    # Verify we have workflow state for this thread so the user can continue.
    try:
        snap = graph.get_state({"configurable": {"thread_id": tid}})
        msgs = snap.values.get("messages", []) or []
        graph_len = len(msgs)
        graph_state_ok = True
    except Exception:
        graph_len = int(data.get("graph_messages_len", 0) or 0)
        graph_state_ok = False

    st.session_state.thread_id = tid
    st.session_state.chat = list(data.get("chat") or [])
    st.session_state.awaiting_resume = bool(data.get("awaiting_resume", False))
    st.session_state.graph_messages_len = graph_len
    st.session_state.initialized = True

    st.session_state.ui_reasoning_title = data.get("ui_reasoning_title")
    st.session_state.ui_reasoning_summary = data.get("ui_reasoning_summary")
    st.session_state.last_interrupt_payload = data.get("last_interrupt_payload")
    st.session_state.user_waiting_for_reviewer = bool(data.get("user_waiting_for_reviewer", False))

    # Clear any in-flight work from the previous thread.
    st.session_state.pending_turn = None

    # Keep the user at the current question after loading.
    st.session_state.scroll_to_bottom = True

    # If graph state is missing, the user can still view the transcript, but
    # cannot safely continue.
    st.session_state["_loaded_graph_state_ok"] = graph_state_ok
    return True


def _inject_techgov_styles() -> None:
        css_path = Path(__file__).with_name("streamlit_app.css")
        try:
                css = css_path.read_text(encoding="utf-8")
        except OSError:
                css = ""
        st.markdown(f"<style>\n{css}\n</style>", unsafe_allow_html=True)


def _render_topbar() -> None:
    st.markdown(
        (
            '<div class="tg-topbar">'
            '  <div class="tg-brand">'
            '    <div class="tg-logo"></div>'
            '    <div class="tg-title">'
            '      <h1>TSG - AI</h1>'
            '      <p>AI Governance &amp; Compliance Audit Platform</p>'
            '    </div>'
            '  </div>'
            '  <div class="tg-user">'
            '    <div class="meta">'
            '      <p class="name">Li Jianguo</p>'
            '      <p class="role">Senior AI Governance Officer</p>'
            '    </div>'
            '    <div class="tg-avatar"></div>'
            '  </div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")


def _inline_markdown_to_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC_RE.sub(r"<em>\1</em>", escaped)
    return escaped


def _markdownish_to_html(text: str) -> str:
    """Very small markdown-ish renderer for our chat bubbles.

    Purpose: keep formatting close to the TechGov template while ensuring we
    render a single pure-HTML block (avoids Streamlit markdown/html edge-cases).
    Supports: paragraphs, line breaks, bullet lists, **bold**, *italic*.
    """

    cleaned = text.strip("\n")
    if not cleaned:
        return ""

    blocks = re.split(r"\n\s*\n", cleaned)
    parts: list[str] = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.splitlines()]
        bullet_lines = []
        non_bullet = []
        for ln in lines:
            stripped = ln.lstrip()
            if (stripped.startswith("- ") or stripped.startswith("* ")) and len(stripped) > 2:
                bullet_lines.append(stripped[2:])
            else:
                non_bullet.append(ln)

        if bullet_lines and not any(s.strip() for s in non_bullet):
            parts.append("<ul>")
            for item in bullet_lines:
                parts.append(f"<li>{_inline_markdown_to_html(item)}</li>")
            parts.append("</ul>")
        else:
            paragraph = "<br>".join(_inline_markdown_to_html(ln) for ln in lines)
            parts.append(f"<p>{paragraph}</p>")

    return "".join(parts)


def _render_chat_message(role: str, content: str) -> None:
        """Render a single chat message in a TechGov-template layout.

        Notes:
        - We keep assistant content as markdown (may include lists/formatting).
        - We escape user content to avoid HTML injection.
        """

        if role == "assistant":
            role_label = "AI Governance Virtual Officer"
            body_html = _markdownish_to_html(content)
            assistant_icon_svg = (
                '<svg class="tg-avatar-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
                #'<path fill="currentColor" d="M12 2l1.45 4.36L18 8l-4.55 1.64L12 14l-1.45-4.36L6 8l4.55-1.64L12 2Zm7 9l.97 2.9L23 15l-3.03 1.1L19 19l-.97-2.9L15 15l3.03-1.1L19 11ZM5 11l.97 2.9L9 15l-3.03 1.1L5 19l-.97-2.9L1 15l3.03-1.1L5 11Z"/>'
                '<g fill="none" stroke="currentColor" stroke-linejoin="round" stroke-width="1.5">'
                '<path stroke-linecap="round" d="M17.478 9h.022a4.5 4.5 0 0 1 2.064 8.5M17.478 9q.021-.247.022-.5a5.5 5.5 0 0 0-10.98-.477M17.478 9a5.5 5.5 0 0 1-1.235 3M6.52 8.023A5 5 0 0 0 4.818 17.5M6.52 8.023Q6.757 8 7 8c1.126 0 2.165.372 3 1"></path>'
                '<path d="m12 14l.258.697c.338.914.507 1.371.84 1.704c.334.334.791.503 1.705.841l.697.258l-.697.258c-.914.338-1.371.507-1.704.84c-.334.334-.503.791-.841 1.705L12 21l-.258-.697c-.338-.914-.507-1.371-.84-1.704c-.334-.334-.791-.503-1.705-.841L8.5 17.5l.697-.258c.914-.338 1.371-.507 1.704-.84c.334-.334.503-.791.841-1.705z"></path>'
                '</g>'
                '</svg>'
            )
            html_block = (
                '<div class="tg-msg tg-msg-assistant">'
                f'  <div class="tg-avatar-dot tg-avatar-assistant">{assistant_icon_svg}</div>'
                '  <div class="tg-msg-body">'
                f'    <div class="tg-msg-role tg-msg-role-assistant">{role_label}</div>'
                f'    <div class="tg-bubble tg-bubble-assistant">{body_html}</div>'
                '  </div>'
                '</div>'
            )
            st.markdown(html_block, unsafe_allow_html=True)
            return

        # reviewer (rendered like user, but labeled explicitly)
        if role == "reviewer":
            role_label = "Reviewer"
            body_html = _markdownish_to_html(content)
            user_icon_svg = (
                '<svg class="tg-avatar-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
                '<circle cx="12" cy="6" r="4" fill="currentColor"></circle>'
                '<path fill="currentColor" d="M20 17.5c0 2.485 0 4.5-8 4.5s-8-2.015-8-4.5S7.582 13 12 13s8 2.015 8 4.5"></path>'
                '</svg>'
            )
            html_block = (
                '<div class="tg-msg tg-msg-user">'
                '  <div class="tg-msg-body tg-msg-body-user">'
                f'    <div class="tg-msg-role tg-msg-role-user">{role_label}</div>'
                f'    <div class="tg-bubble tg-bubble-user">{body_html}</div>'
                '  </div>'
                f'  <div class="tg-avatar-dot tg-avatar-user">{user_icon_svg}</div>'
                '</div>'
            )
            st.markdown(html_block, unsafe_allow_html=True)
            return

        # user
        role_label = "User · {Name}"
        body_html = _markdownish_to_html(content)
        user_icon_svg = (
            '<svg class="tg-avatar-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            #'<path fill="currentColor" d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Zm0 2c-4.42 0-8 2.24-8 5v1h16v-1c0-2.76-3.58-5-8-5Z"/>'
            '<circle cx="12" cy="6" r="4" fill="currentColor"></circle>'
            '<path fill="currentColor" d="M20 17.5c0 2.485 0 4.5-8 4.5s-8-2.015-8-4.5S7.582 13 12 13s8 2.015 8 4.5"></path>'
            '</svg>'
        )
        html_block = (
                '<div class="tg-msg tg-msg-user">'
                '  <div class="tg-msg-body tg-msg-body-user">'
                f'    <div class="tg-msg-role tg-msg-role-user">{role_label}</div>'
                f'    <div class="tg-bubble tg-bubble-user">{body_html}</div>'
                '  </div>'
            f'  <div class="tg-avatar-dot tg-avatar-user">{user_icon_svg}</div>'
                '</div>'
        )
        st.markdown(html_block, unsafe_allow_html=True)


def _get_query_params() -> Dict[str, Any]:
    """Compatibility wrapper across Streamlit versions."""
    # Newer Streamlit exposes st.query_params (Mapping[str, str|list[str]])
    qp = getattr(st, "query_params", None)
    if qp is not None:
        # Convert to plain dict to keep downstream logic simple
        return dict(qp)
    # Older Streamlit uses experimental_get_query_params (dict[str, list[str]])
    getter = getattr(st, "experimental_get_query_params", None)
    if getter is not None:
        return getter()  # type: ignore[no-any-return]
    return {}


def _query_flag(name: str, default: bool = False) -> bool:
    """Return True when URL query param is set (e.g. ?debug=1)."""
    params = _get_query_params()
    raw = params.get(name)
    if raw is None:
        return default
    # Streamlit may return a str, or a list[str]
    value = raw[0] if isinstance(raw, list) and raw else raw
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "t", "yes", "y", "on"):
        return True
    if text in ("0", "false", "f", "no", "n", "off"):
        return False
    # Unknown value: treat as enabled if present (common pattern: ?debug)
    return True


def _query_value(name: str, default: str | None = None) -> str | None:
    """Return a single query param value as a string (best-effort)."""
    params = _get_query_params()
    raw = params.get(name)
    if raw is None:
        return default
    value = raw[0] if isinstance(raw, list) and raw else raw
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _get_role() -> str:
    """Role toggle via URL query param.

    Examples:
      - normal user:     (no param) or ?role=user
      - reviewer view:   ?role=reviewer
    """
    role = (_query_value("role") or "").strip().lower()
    if role in ("reviewer", "review", "approver"):
        return "reviewer"
    return "user"


def _is_reviewer() -> bool:
    return _get_role() == "reviewer"


@st.cache_resource
def get_graph():
    settings = Settings.from_env()
    return build_graph(settings=settings)


def ensure_session():
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = str(uuid.uuid4())
    if "awaiting_resume" not in st.session_state:
        st.session_state.awaiting_resume = False
    if "chat" not in st.session_state:
        st.session_state.chat = []  # list[{"role":..., "content":...}]
    if "graph_messages_len" not in st.session_state:
        st.session_state.graph_messages_len = 0
    if "initialized" not in st.session_state:
        st.session_state.initialized = False

    # UI reasoning summary (rendered in the transcript after each user answer)
    if "ui_reasoning_title" not in st.session_state:
        st.session_state.ui_reasoning_title = None
    if "ui_reasoning_summary" not in st.session_state:
        st.session_state.ui_reasoning_summary = None

    # Pending turn staging (to show user answer + quick feedback immediately)
    # Structure: {"user_text": str, "resume": bool, "ack_id": str}
    if "pending_turn" not in st.session_state:
        st.session_state.pending_turn = None
    # Latest interrupt payload dict (so we can generate a fast acknowledgement)
    if "last_interrupt_payload" not in st.session_state:
        st.session_state.last_interrupt_payload = None

    # Scroll helper: when True, we auto-scroll to the latest (current) question.
    # Streamlit reruns reset scroll position back to the top, which is jarring
    # in chat-style apps. We only auto-scroll when we know we just appended new
    # content (user answer, fast feedback, reasoning panel, next question, ...).
    if "scroll_to_bottom" not in st.session_state:
        st.session_state.scroll_to_bottom = False

    # Reviewer pending UX helper (normal user view)
    if "user_waiting_for_reviewer" not in st.session_state:
        st.session_state.user_waiting_for_reviewer = False

    # Auto-load guard (prevents repeatedly trying to load from URL params).
    if "_auto_load_done" not in st.session_state:
        st.session_state._auto_load_done = False


def _sync_ui_reasoning_from_state(result_state: Dict[str, Any]) -> None:
    """Pull the latest UI reasoning summary from the graph state into session_state."""

    title = result_state.get("ui_reasoning_title")
    summary = result_state.get("ui_reasoning_summary")
    # We only overwrite when we actually have a value; this prevents clobbering
    # a useful previous summary with a transient None (e.g., from a node that
    # doesn't set it).
    if title is not None:
        st.session_state.ui_reasoning_title = title
    if summary is not None:
        st.session_state.ui_reasoning_summary = summary


def _render_reasoning_panel(title: str | None, content: str) -> None:
    if not content:
        return
    title_html = html.escape(title or "Reasoning summary")
    body_html = _markdownish_to_html(content)
    st.markdown(
        (
            '<div class="tg-reasoning">'
            f'  <div class="tg-reasoning-title">{title_html}</div>'
            f'  <div class="tg-reasoning-body">{body_html}</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


def _render_chat_bottom_anchor() -> None:
    """Anchor element used for auto-scrolling to the latest message."""
    st.markdown('<div id="tg-chat-bottom" style="height:1px;"></div>', unsafe_allow_html=True)


def _maybe_autoscroll_to_bottom() -> None:
    """Auto-scroll the main page to the latest chat message.

    Streamlit reruns reset scroll position to the top. We mitigate that by
    scrolling to a hidden anchor at the bottom of the chat *only when* we know
    we just appended new content.
    """

    if not st.session_state.get("scroll_to_bottom", False):
        return

    # Best-effort: try multiple selectors across Streamlit versions.
    components.html(
        """
        <script>
        (function () {
          try {
            const doc = window.parent.document;
            const el = doc.getElementById('tg-chat-bottom');
            if (el) {
              el.scrollIntoView({ behavior: 'smooth', block: 'end' });
              return;
            }
            // Fallback: scroll the main container
            const main = doc.querySelector('section.main') || doc.querySelector('[data-testid="stAppViewContainer"]');
            if (main) {
              main.scrollTo(0, main.scrollHeight);
            }
          } catch (e) {
            // no-op
          }
        })();
        </script>
        """,
        height=0,
    )

    # Reset the flag so we don't fight the user if they scroll up manually.
    st.session_state.scroll_to_bottom = False


def sidebar(graph):
    st.sidebar.markdown("## TSG - AI")
    st.sidebar.caption("AI Governance & Compliance Audit Platform")

    # Disable controls while we are processing a staged turn.
    processing = st.session_state.get("pending_turn") is not None

    if st.sidebar.button("New TSG for AI Session"):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.awaiting_resume = False
        st.session_state.chat = []
        st.session_state.graph_messages_len = 0
        st.session_state.initialized = False
        st.session_state.ui_reasoning_title = None
        st.session_state.ui_reasoning_summary = None
        st.session_state.pending_turn = None
        st.session_state.last_interrupt_payload = None
        st.session_state.user_waiting_for_reviewer = False
        st.rerun()

    # ------------------------------------------------------------------
    # Session details (always visible)
    # ------------------------------------------------------------------
    st.sidebar.divider()
    #st.sidebar.subheader("TSG AI Case Details")
    st.sidebar.write("**TSG AI Case ID**")
    # Streamlit's `code()` inherits the sidebar's forced light text color.
    # Render a small HTML block so the Case ID can be shown in black/dark text.
    st.sidebar.markdown(
        f'<div class="tg-caseid-box">{html.escape(str(st.session_state.thread_id))}</div>',
        unsafe_allow_html=True,
    )

    # ------------------------------------------------------------------
    # Session history
    # - Normal users: search/open by Case ID (resume)
    # - Reviewers:    dashboard-style lists (In Progress / Approved / Rejected)
    # ------------------------------------------------------------------
    if _is_reviewer():
        # (Reviewer dashboard placeholder; can be wired to real persisted sessions later)
        st.sidebar.markdown(
            """
                <div class="tg-card" style="opacity:0.75;">
                    <div class="row">
                        <span class="tg-badge blue">In Progress</span>
                        <span class="tg-time">14:20</span>
                    </div>
                    <p class="tg-titleline">Financial Anti-Fraud Model Compliance Audit</p>
                    <p class="tg-subline">Analyzing data privacy risk points…</p>
                </div>
                <div class="tg-card">
                    <div class="row">
                        <span class="tg-badge green">Approved</span>
                        <span class="tg-time">Yesterday</span>
                    </div>
                    <p class="tg-titleline">Recommendation Algorithm Ethics Review V2</p>
                    <p class="tg-subline">Report ID: TG-20251220</p>
                </div>
                <div class="tg-card">
                    <div class="row">
                        <span class="tg-badge red">Rejected</span>
                        <span class="tg-time">3 days ago</span>
                    </div>
                    <p class="tg-titleline">Core Business Logic Transparency Analysis</p>
                    <p class="tg-subline">Risk Level: High</p>
                </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.subheader("Search history")
        st.sidebar.caption("Paste a Case ID to resume a saved session.")

        def _on_history_load_click() -> None:
            # NOTE: Streamlit callbacks run before the script body on rerun.
            # Clearing a widget-backed key here is safe.
            target = (st.session_state.get("history_search_input") or "").strip()
            if not target:
                st.session_state["_history_load_feedback"] = ("warning", "Enter a Case ID first.")
                return

            # Save current session before switching.
            _persist_ui_session(graph=graph)

            data = _load_ui_session(target)
            if not data:
                st.session_state["_history_load_feedback"] = (
                    "error",
                    "No saved session found for that Case ID.",
                )
                return

            ok = _apply_loaded_session(data, graph=graph)
            if ok:
                st.session_state["history_search_input"] = ""
                _persist_ui_session(graph=graph)
                st.session_state["_history_load_feedback"] = ("success", "Session loaded.")

        # Ensure the widget key exists before instantiation.
        st.session_state.setdefault("history_search_input", "")

        search_value = st.sidebar.text_input(
            "Search session history …",
            placeholder="Case ID",
            label_visibility="collapsed",
            key="history_search_input",
            disabled=processing,
        )
        st.sidebar.button(
            "Load session",
            disabled=processing,
            key="history_load_btn",
            on_click=_on_history_load_click,
        )

        feedback = st.session_state.pop("_history_load_feedback", None)
        if isinstance(feedback, (tuple, list)) and len(feedback) == 2:
            level, message = feedback
            if level == "warning":
                st.sidebar.warning(message)
            elif level == "error":
                st.sidebar.error(message)
            elif level == "success":
                st.sidebar.success(message)

    ## Optional: submission text helper (paste here instead of chat)
    #st.sidebar.divider()
    #st.sidebar.subheader("Submission text (optional)")
    #submission_text = st.sidebar.text_area(
    #    "Paste text here (useful for long answers).",
    #    height=160,
    #    placeholder="Paste here if you don't want to paste into chat...",
    #    key="sidebar_submission_text",
    #    disabled=processing,
    #)

    #def _on_sidebar_attach_send_click() -> None:
    #    # Callbacks run before the rest of the script on rerun, so it's safe
    #    # to mutate widget-backed keys here.
    #    st.session_state["_sidebar_send_text"] = st.session_state.get("sidebar_submission_text", "")
    #    st.session_state["_sidebar_send_clicked"] = True
    #    st.session_state["sidebar_submission_text"] = ""

    #st.sidebar.button(
    #    "Attach / Send",
    #    disabled=processing,
    #    on_click=_on_sidebar_attach_send_click,
    #)

    # if st.session_state.pop("_sidebar_send_clicked", False):
    #     """Sidebar helper for long text.

    #     Behavior:
    #     - If we are currently waiting for an answer (awaiting_resume=True), this
    #       will be treated as the answer to the *current* question.
    #     - Otherwise, we attach it to intake.submission_text for later checklist
    #       context.
    #     """
    #     text = (st.session_state.pop("_sidebar_send_text", "") or "").strip()
    #     if not text:
    #         st.sidebar.warning("Paste some text first.")
    #         return

    #     config = {"configurable": {"thread_id": st.session_state.thread_id}}

    #     if st.session_state.get("awaiting_resume", False):
    #         # Reviewer decision is a special human-only step. Normal users
    #         # should not be able to submit it via the sidebar.
    #         payload = st.session_state.get("last_interrupt_payload")
    #         ptype = str(payload.get("type") or "").strip().lower() if isinstance(payload, dict) else ""
    #         if ptype == "review_decision" and not _is_reviewer():
    #             st.sidebar.warning(
    #                 "Final decision is pending reviewer approval. "
    #                 "Open the app with ?role=reviewer to submit a decision."
    #             )
    #             return

    #         # Treat as the answer to the current question.
    #         display_text = text
    #         if len(display_text) > 1200:
    #             display_text = display_text[:1200].rstrip() + "\n\n...(truncated; full text was sent)..."
    #         append_message("user", display_text)

    #         ack = _fast_feedback_message(st.session_state.get("last_interrupt_payload"), text)
    #         ack_id = append_message("assistant", ack)

    #         st.session_state.pending_turn = {
    #             "user_text": text,
    #             "resume": True,
    #             "ack_id": ack_id,
    #         }

    #         # Auto-scroll to keep the user at the current question area.
    #         st.session_state.scroll_to_bottom = True
    #         _persist_ui_session(graph=graph)
    #         st.rerun()

    #     # Not awaiting an answer: attach to case state for later.
    #     try:
    #         snap = graph.get_state(config)
    #         intake = dict(snap.values.get("intake", {}) or {})
    #     except Exception:
    #         intake = {}

    #     intake["submission_text"] = text
    #     graph.update_state(config, {"intake": intake})
    #     st.sidebar.success("Attached to case state (intake.submission_text).")
    #     _persist_ui_session(graph=graph)
    #     st.rerun()

    st.sidebar.divider()
    if _query_flag("debug", default=False):
        with st.sidebar.expander("Debug / Audit"):
            config = {"configurable": {"thread_id": st.session_state.thread_id}}
            try:
                snap = graph.get_state(config)
                st.write("**Phase**:", snap.values.get("phase"))
                st.write("**Missing fields**:", snap.values.get("missing_fields"))
                st.write("**Reviewer decision**:", snap.values.get("reviewer_decision"))
                if snap.values.get("checklist_report"):
                    st.write("**Checklist report**")
                    st.json(snap.values.get("checklist_report"))

                st.write("**Audit log**")
                st.json(snap.values.get("audit_log", []))

                reasoning_items = []
                class_reason = snap.values.get("classification_reasoning")
                if class_reason:
                    reasoning_items.append(("Classification reasoning", class_reason))
                checklist_reason = snap.values.get("checklist_reasoning")
                if checklist_reason:
                    reasoning_items.append(("Checklist reasoning", checklist_reason))
                flow_reason = snap.values.get("flowchart_reasoning")
                if flow_reason:
                    reasoning_items.append(("Flowchart reasoning", flow_reason))
                if reasoning_items:
                    st.write("**LLM reasoning summaries**")
                    for title, text in reasoning_items:
                        with st.expander(title, expanded=False):
                            st.markdown(text)
            except Exception as e:
                st.warning(f"No state yet for this thread: {e}")

def render_chat():
    """Render the chat transcript.

    In addition to normal `user` / `assistant` messages, we support a special
    `reasoning` block which is rendered as a panel *between* the user's answer
    and the next assistant question.
    """
    for m in st.session_state.chat:
        role = m.get("role")
        if role == "reasoning":
            _render_reasoning_panel(m.get("title"), m.get("content", ""))
        else:
            _render_chat_message(m["role"], m["content"])


def append_message(role: str, content: str, *, msg_id: str | None = None) -> str:
    """Append a message to the local UI transcript and return its id."""
    if msg_id is None:
        msg_id = str(uuid.uuid4())
    st.session_state.chat.append({"id": msg_id, "role": role, "content": content})
    return msg_id


def append_reasoning(title: str | None, content: str, *, msg_id: str | None = None) -> str | None:
    """Append a reasoning summary UI block into the chat transcript."""
    if not content:
        return None
    if msg_id is None:
        msg_id = str(uuid.uuid4())
    st.session_state.chat.append(
        {"id": msg_id, "role": "reasoning", "title": title, "content": content}
    )
    return msg_id


def _update_message_content(msg_id: str, new_content: str) -> None:
    """Update a previously appended message by id (best‑effort)."""
    if not msg_id:
        return
    for m in st.session_state.chat:
        if m.get("id") == msg_id:
            m["content"] = new_content
            return


def _format_interrupt_question(payload: Any) -> str:
    if isinstance(payload, dict) and payload.get("question"):
        q = str(payload.get("question") or "")
        hint = str(payload.get("hint") or "")
        return q + (f"\n\n*{hint}*" if hint else "")
    return str(payload)


_REVIEW_PROMPT_RE = re.compile(r"\n\s*\nReviewer decision\?.*\Z", re.IGNORECASE | re.DOTALL)
_AI_SUGGESTED_DECISION_RE = re.compile(
    r"AI\s*suggested\s*decision:\s*\*\*([A-Z_]+)\*\*", re.IGNORECASE
)


def _strip_review_prompt(question_text: str) -> str:
    """Remove the trailing 'Reviewer decision?' prompt from the review interrupt.

    The review node packs the *AI suggested decision* block and the reviewer
    prompt into a single interrupt question string. In the UI we want to show
    the AI block, but replace the reviewer prompt with dedicated radio buttons.
    """
    text = (question_text or "").strip()
    if not text:
        return ""
    cleaned = _REVIEW_PROMPT_RE.sub("", text).rstrip()
    return cleaned if cleaned else text


def _ai_suggested_decision_from_text(text: str) -> str | None:
    """Best-effort parse of the AI suggested decision from the review block."""
    if not text:
        return None
    m = _AI_SUGGESTED_DECISION_RE.search(text)
    if m:
        return (m.group(1) or "").strip().upper() or None
    # Fallback: without bold formatting
    m2 = re.search(r"AI\s*suggested\s*decision:\s*([A-Z_]+)", text, re.IGNORECASE)
    if m2:
        return (m2.group(1) or "").strip().upper() or None
    return None


def _append_interrupt_question(payload: Any) -> None:
    """Append the next question (interrupt) and remember its payload."""
    st.session_state.last_interrupt_payload = payload if isinstance(payload, dict) else None

    # Special handling: reviewer decision should NOT show the in-chat prompt.
    # Instead we show the AI recommendation block here, then render reviewer-only
    # radio buttons below the transcript.
    if isinstance(payload, dict) and str(payload.get("type") or "").strip().lower() == "review_decision":
        q = str(payload.get("question") or "")
        cleaned = _strip_review_prompt(q)
        if cleaned.strip():
            append_message("assistant", cleaned.strip())

        # Normal users should not see reviewer controls; show a clear status.
        if not _is_reviewer():
            m = _AI_SUGGESTED_DECISION_RE.search(q or "")
            suggested = (m.group(1) if m else "").strip().upper()
            if suggested and suggested != "APPROVE":
                append_message(
                    "assistant",
                    "Final decision is pending reviewer approval. "
                    "If you'd like, you can update answers for remaining FAIL/UNKNOWN items below before the reviewer decides.",
                )
            else:
                append_message("assistant", "Final decision is pending reviewer approval.")
        return

    append_message("assistant", _format_interrupt_question(payload))


def _fast_feedback_message(payload: Any, answer_text: str) -> str:
    """Generate a quick, non‑LLM acknowledgement message.

    This is displayed immediately after the user submits an answer, so the UI
    feels responsive while the graph/LLM does heavier work.
    """
    # Default
    fallback = "Noted. Thanks."

    if not isinstance(payload, dict):
        return fallback

    ptype = str(payload.get("type") or "").strip().lower()
    answer_norm = (answer_text or "").strip().lower()

    if ptype == "intake_question":
        field = str(payload.get("field") or "").strip() or "item"
        return f"Got it. ({field} recorded.)"

    if ptype == "followup_question":
        return "Noted. Thanks."

    if ptype == "process_description":
        return "Thanks — generating a draft flowchart now."

    if ptype == "flowchart_confirm":
        if answer_norm in ("yes", "y", "correct", "confirmed"):
            return "Confirmed. Moving to reviewer decision step."
        return "Thanks — I’ll update the draft flowchart now."

    if ptype == "review_decision":
        # Special: user requested to update FAIL/UNKNOWN answers.
        raw = (answer_text or "").strip()
        if raw == UPDATE_ANSWERS_TOKEN:
            return "Okay — let's update the remaining FAIL/UNKNOWN items and re-run the checklist."

        # Mirror the mapping in tsg_officer.graph.nodes.review
        allowed = {
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
        key = raw.lower().replace("-", "_").replace(" ", "_")
        decision = allowed.get(key)
        if decision is None and key.upper() in ("APPROVE", "CONDITIONAL_APPROVE", "REJECT", "NEED_INFO"):
            decision = key.upper()
        if decision is None:
            decision = "NEED_INFO"
        return f"Reviewer decision recorded: **{decision}**"

    return fallback


def _review_decision_pending() -> bool:
    """True when the workflow is waiting for a reviewer decision interrupt."""
    payload = st.session_state.get("last_interrupt_payload")
    ptype = str(payload.get("type") or "").strip().lower() if isinstance(payload, dict) else ""
    return bool(st.session_state.get("awaiting_resume", False)) and ptype == "review_decision"


def _process_pending_turn(graph) -> None:
    """Run the graph for a staged user turn and append results to the transcript."""
    pending = st.session_state.get("pending_turn") or {}
    display_text = str(pending.get("user_text") or "").strip()
    resume_value = str(pending.get("resume_value") or display_text).strip()
    message_content = str(pending.get("message_content") or display_text).strip()
    if not message_content:
        st.session_state.pending_turn = None
        return

    resume = bool(pending.get("resume"))
    ack_id = str(pending.get("ack_id") or "").strip() or None

    config = {"configurable": {"thread_id": st.session_state.thread_id}}

    # Run graph: either resume an interrupt, or start a new turn by appending a user message to state
    if resume:
        result = graph.invoke(
            Command(
                resume=resume_value,
                update={"messages": [{"role": "user", "content": message_content}]},
            ),
            config=config,
        )
    else:
        result = graph.invoke({"messages": [{"role": "user", "content": message_content}]}, config=config)

    # Sync the reasoning panel from the graph state
    _sync_ui_reasoning_from_state(result)

    # Collect NEW assistant messages returned by the graph state (diff by length)
    msgs: List[Dict[str, Any]] = result.get("messages", []) or []
    start = int(st.session_state.get("graph_messages_len", 0) or 0)
    new_assistant: List[Dict[str, Any]] = []
    for msg in msgs[start:]:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            new_assistant.append(msg)

    # Update the immediate acknowledgement bubble to match the actual first assistant message
    if ack_id and new_assistant:
        _update_message_content(ack_id, str(new_assistant[0].get("content", "")))
        new_assistant = new_assistant[1:]

    # Insert readable reasoning summary panel AFTER the feedback bubble and BEFORE the next question(s)
    reasoning_summary = result.get("ui_reasoning_summary")
    if isinstance(reasoning_summary, str) and reasoning_summary.strip():
        append_reasoning(result.get("ui_reasoning_title"), reasoning_summary)

    # Append any remaining assistant messages (these represent the next step / outputs)
    for msg in new_assistant:
        append_message("assistant", str(msg.get("content", "")))

    st.session_state.graph_messages_len = len(msgs)

    # Handle interrupt payload (next question)
    if "__interrupt__" in result and result["__interrupt__"]:
        intr = result["__interrupt__"][0]
        payload = getattr(intr, "value", intr)
        _append_interrupt_question(payload)
        st.session_state.awaiting_resume = True
    else:
        st.session_state.awaiting_resume = False
        st.session_state.last_interrupt_payload = None

    # Clear pending turn
    st.session_state.pending_turn = None


def bootstrap_case(graph):
    """Initialize the case state + run router once to get greeting / first question."""
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    init_state = new_case_state(st.session_state.thread_id)
    result = graph.invoke(init_state, config=config)

    # Sync UI reasoning panel (usually empty on first run)
    _sync_ui_reasoning_from_state(result)

    msgs = result.get("messages", []) or []
    for msg in msgs:
        append_message(msg["role"], msg["content"])

    st.session_state.graph_messages_len = len(msgs)

    # If the first run immediately interrupts (asks a question), surface it
    if "__interrupt__" in result and result["__interrupt__"]:
        intr = result["__interrupt__"][0]
        payload = getattr(intr, "value", intr)
        _append_interrupt_question(payload)
        st.session_state.awaiting_resume = True
    else:
        st.session_state.last_interrupt_payload = None

    return result



def main():
    ensure_session()
    graph = get_graph()
    _inject_techgov_styles()
    sidebar(graph)

    # --------------------------------------------------------------
    # Auto-load a session from URL query params (useful for sharing links).
    # Examples:
    #   - ?case=<thread_id>
    #   - ?thread_id=<thread_id>
    #
    # This is particularly useful for reviewers (who don't see the sidebar
    # search box) to open a specific case by ID.
    # --------------------------------------------------------------
    if not st.session_state.get("_auto_load_done", False):
        candidate = (
            _query_value("case")
            or _query_value("case_id")
            or _query_value("thread")
            or _query_value("thread_id")
        )
        candidate = (candidate or "").strip()

        if candidate:
            data = _load_ui_session(candidate)
            if data:
                _apply_loaded_session(data, graph=graph)
                _persist_ui_session(graph=graph)
                st.session_state._auto_load_done = True
                st.rerun()

        # Mark done even if candidate was missing or didn't load,
        # so we don't loop on every rerun.
        st.session_state._auto_load_done = True

    # If we loaded a transcript but could not find durable workflow state, warn the user.
    # (The transcript is still viewable, but the case cannot be safely resumed.)
    if st.session_state.get("_loaded_graph_state_ok") is False:
        st.warning(
            "⚠️ This session transcript was loaded, but the workflow state was not found in the checkpoint database. "
            "You can view the history, but you may not be able to continue answering questions until the checkpoint DB is restored."
        )

    # Init case (once)
    if not st.session_state.initialized:
        # bootstrap_case() already appends the initial graph messages + first interrupt question
        # into the local UI chat history.
        bootstrap_case(graph)
        st.session_state.initialized = True
        _persist_ui_session(graph=graph)

    render_chat()
    # Anchor + optional auto-scroll (keeps user at the current question after reruns)
    _render_chat_bottom_anchor()
    _maybe_autoscroll_to_bottom()

    processing = st.session_state.get("pending_turn") is not None

    # True when the workflow is waiting for a reviewer decision interrupt.
    review_pending = _review_decision_pending()

    # ------------------------------------------------------------------
    # Input / action area
    # ------------------------------------------------------------------
    if processing:
        st.caption("⏳ Processing your last answer…")

    # Reviewer decision is handled via dedicated UI controls (radio buttons)
    # and is only visible when the URL includes ?role=reviewer.
    if review_pending and not processing:
        if _is_reviewer():
            st.markdown("### Reviewer decision")
            with st.form("reviewer_decision_form", clear_on_submit=False):
                decision = st.radio(
                    "Select a decision",
                    ("APPROVE", "CONDITIONAL_APPROVE", "REJECT", "NEED_INFO"),
                    horizontal=True,
                    key="reviewer_decision_choice",
                )
                submit_decision = st.form_submit_button("Submit decision")

            if submit_decision:
                # Show the reviewer's choice in the transcript (as reviewer),
                # then run the graph resume in the next rerun.
                append_message("reviewer", str(decision))

                ack = _fast_feedback_message(
                    st.session_state.get("last_interrupt_payload"), str(decision)
                )
                ack_id = append_message("assistant", ack)

                st.session_state.pending_turn = {
                    "user_text": str(decision),
                    "resume": True,
                    "ack_id": ack_id,
                }

                st.session_state.scroll_to_bottom = True
                _persist_ui_session(graph=graph)
                st.rerun()
        else:
            # Normal user view: decision pending reviewer approval.
            st.caption("🔒 Final decision is pending reviewer approval.")

            # If the AI suggested decision is NOT APPROVE, allow the submitter
            # to either wait for the reviewer or revisit FAIL/UNKNOWN items.
            payload = st.session_state.get("last_interrupt_payload")
            suggested: str | None = None
            if isinstance(payload, dict):
                suggested = _ai_suggested_decision_from_text(str(payload.get("question") or ""))

            # Fallback: read from graph state if we couldn't parse the interrupt text.
            if not suggested:
                try:
                    config = {"configurable": {"thread_id": st.session_state.thread_id}}
                    snap = graph.get_state(config)
                    report = snap.values.get("checklist_report") or {}
                    if isinstance(report, dict):
                        suggested = str(report.get("overall_recommendation") or "").strip().upper() or None
                except Exception:
                    suggested = None

            if suggested and suggested != "APPROVE":
                st.markdown("### Next actions")
                cols = st.columns(2)
                wait_clicked = cols[0].button(
                    "Wait for reviewer decision",
                    use_container_width=True,
                    key="wait_for_reviewer_btn",
                )
                update_clicked = cols[1].button(
                    "Update answers for FAIL/UNKNOWN questions",
                    use_container_width=True,
                    key="update_fail_unknown_btn",
                )

                if wait_clicked:
                    st.session_state["user_waiting_for_reviewer"] = True
                    _persist_ui_session(graph=graph)

                if update_clicked:
                    # If the user previously clicked "wait", clear that state
                    # since they are now choosing to revise their answers.
                    st.session_state["user_waiting_for_reviewer"] = False

                    # Record the user's choice in the transcript for auditability.
                    action_label = "Update answers for FAIL/UNKNOWN questions"
                    append_message("user", action_label)

                    # Immediate feedback (no LLM) while we resume the graph.
                    ack_id = append_message(
                        "assistant",
                        "Okay — switching to update mode so you can revise the remaining FAIL/UNKNOWN items…",
                    )

                    # Resume the pending review interrupt with a special token.
                    st.session_state.pending_turn = {
                        "user_text": action_label,
                        "resume": True,
                        "ack_id": ack_id,
                        "resume_value": UPDATE_ANSWERS_TOKEN,
                        "message_content": action_label,
                    }

                    st.session_state.scroll_to_bottom = True
                    _persist_ui_session(graph=graph)
                    st.rerun()

                if st.session_state.get("user_waiting_for_reviewer", False):
                    st.info("Waiting for the reviewer’s final decision…")

        # Do not show the normal chat input while awaiting reviewer decision.
        submitted = False
        user_text = ""

    else:
        with st.form("chat_form", clear_on_submit=True):
            user_text = st.text_area(
                "Type your message…",
                height=80,
                placeholder="Type your message…",
                label_visibility="collapsed",
                disabled=processing,
            )

            submitted = st.form_submit_button("Send", disabled=processing)

    # --------------------------------------------------------------
    # Stage user turn so the UI updates immediately
    #
    # Goal:
    #   - Show the user’s answer + a quick acknowledgement instantly
    #   - Then take time to run the graph + generate the reasoning panel
    #   - Finally show the reasoning panel and the next question
    # --------------------------------------------------------------
    if submitted and not processing:
        user_text = (user_text or "").strip()
        if not user_text:
            return

        # 1) Record user answer immediately
        append_message("user", user_text)

        # 2) Show a fast acknowledgement immediately (no LLM)
        ack = _fast_feedback_message(st.session_state.get("last_interrupt_payload"), user_text)
        ack_id = append_message("assistant", ack)

        # 3) Stage graph execution for the next rerun (may take time)
        st.session_state.pending_turn = {
            "user_text": user_text,
            "resume": bool(st.session_state.get("awaiting_resume", False)),
            "ack_id": ack_id,
        }

        # Keep the viewport at the latest message after the rerun.
        st.session_state.scroll_to_bottom = True

        _persist_ui_session(graph=graph)
        st.rerun()

    # If we have a staged turn, run it now (may take time)
    if processing:
        with st.spinner("Working…"):
            _process_pending_turn(graph)

        # We just appended reasoning + next question; auto-scroll to it.
        st.session_state.scroll_to_bottom = True
        _persist_ui_session(graph=graph)
        st.rerun()


if __name__ == "__main__":
    main()
