from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # loads .env into os.environ


import json
import html
import re
from pathlib import Path
import uuid
from typing import Any, Dict, List

import streamlit as st

from langgraph.types import Command

from tsg_officer.config import Settings
from tsg_officer.graph import build_graph
from tsg_officer.state import new_case_state


st.set_page_config(page_title="TSG - AI", page_icon="✅", layout="wide")


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


def sidebar(graph):
    st.sidebar.markdown("## TSG - AI")
    st.sidebar.caption("AI Governance & Compliance Audit Platform")

    if st.sidebar.button("New TSG for AI Session"):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.awaiting_resume = False
        st.session_state.chat = []
        st.session_state.initialized = False
        st.rerun()

    # Hidden by default; enable with URL param like ?history=1
    if _query_flag("history", default=False):

        st.sidebar.divider()
        st.sidebar.subheader("Session details")
        st.sidebar.write("**Thread / Case ID**")
        st.sidebar.code(st.session_state.thread_id)

        st.sidebar.text_input(
            "Search session history …",
            value="",
            placeholder="Search history…",
            label_visibility="collapsed",
        )

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

    # Optional: submission text helper (paste here instead of chat)
    st.sidebar.divider()
    st.sidebar.subheader("Submission text (optional)")
    submission_text = st.sidebar.text_area(
        "Paste submission text here (stored in case state via update).",
        height=160,
        placeholder="Paste here if you don't want to paste into chat...",
    )
    if st.sidebar.button("Attach submission text"):
        """When the user attaches submission text from the sidebar, update the case
        state and, if appropriate, feed it as the answer to the current intake
        question.  This enables end‑to‑end progression without requiring an extra
        chat message.  We also update the local chat history to reflect any
        new assistant messages returned by the graph."""
        if submission_text.strip():
            config = {"configurable": {"thread_id": st.session_state.thread_id}}
            # Merge into existing intake to avoid overwriting other collected fields
            try:
                snap = graph.get_state(config)
                intake = dict(snap.values.get("intake", {}) or {})
            except Exception:
                intake = {}
            intake["submission_text"] = submission_text
            # Persist the updated intake in the graph state
            graph.update_state(config, {"intake": intake})

            # If we're currently paused waiting for a user answer (awaiting_resume),
            # resume the graph with the submission text as the user's reply.
            if st.session_state.get("awaiting_resume", False):
                try:
                    result = graph.invoke(
                        Command(
                            resume=submission_text,
                            update={"messages": [{"role": "user", "content": submission_text}]},
                        ),
                        config=config,
                    )

                    msgs: List[Dict[str, Any]] = result.get("messages", []) or []
                    start_idx = int(st.session_state.get("graph_messages_len", 0) or 0)
                    for msg in msgs[start_idx:]:
                        if msg.get("role") == "assistant":
                            st.session_state.chat.append({"role": "assistant", "content": msg.get("content", "")})
                    st.session_state.graph_messages_len = len(msgs)

                    if "__interrupt__" in result and result["__interrupt__"]:
                        intr = result["__interrupt__"][0]
                        payload = getattr(intr, "value", intr)
                        if isinstance(payload, dict) and payload.get("question"):
                            q = payload["question"]
                            hint = payload.get("hint", "")
                            content = q + (f"\n\n*{hint}*" if hint else "")
                        else:
                            content = str(payload)
                        st.session_state.chat.append({"role": "assistant", "content": content})
                        st.session_state.awaiting_resume = True
                    else:
                        st.session_state.awaiting_resume = False
                except Exception:
                    st.session_state.awaiting_resume = False

            st.sidebar.success("Attached to case state (intake.submission_text).")
            st.rerun()

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
    for m in st.session_state.chat:
        _render_chat_message(m["role"], m["content"])


def append_message(role: str, content: str):
    st.session_state.chat.append({"role": role, "content": content})


def bootstrap_case(graph):
    """Initialize the case state + run router once to get greeting / first question."""
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    init_state = new_case_state(st.session_state.thread_id)
    result = graph.invoke(init_state, config=config)

    msgs = result.get("messages", []) or []
    for msg in msgs:
        append_message(msg["role"], msg["content"])

    st.session_state.graph_messages_len = len(msgs)

    # If the first run immediately interrupts (asks a question), surface it
    if "__interrupt__" in result and result["__interrupt__"]:
        intr = result["__interrupt__"][0]
        payload = getattr(intr, "value", intr)
        if isinstance(payload, dict) and payload.get("question"):
            q = payload["question"]
            hint = payload.get("hint", "")
            content = q + (f"\n\n*{hint}*" if hint else "")
        else:
            content = str(payload)

        append_message("assistant", content)
        st.session_state.awaiting_resume = True

    return result



def main():
    ensure_session()
    graph = get_graph()
    _inject_techgov_styles()
    sidebar(graph)

    # Init case (once)
    if not st.session_state.initialized:
        # bootstrap_case() already appends the initial graph messages + first interrupt question
        # into the local UI chat history.
        bootstrap_case(graph)
        st.session_state.initialized = True

    render_chat()

    user_text = st.chat_input("Type your message…")
    if not user_text:
        return

    # Always display the user's message immediately
    append_message("user", user_text)
    _render_chat_message("user", user_text)

    config = {"configurable": {"thread_id": st.session_state.thread_id}}

    # Run graph: either resume an interrupt, or start a new turn by appending a user message to state
    if st.session_state.awaiting_resume:
        result = graph.invoke(Command(resume=user_text, update={"messages": [{"role": "user", "content": user_text}]}), config=config)
    else:
        result = graph.invoke({"messages": [{"role": "user", "content": user_text}]}, config=config)

    # Handle interrupt payload (next question)
    if "__interrupt__" in result and result["__interrupt__"]:
        intr = result["__interrupt__"][0]
        payload = getattr(intr, "value", intr)  # Interrupt.value per langgraph.types docs
        if isinstance(payload, dict) and payload.get("question"):
            q = payload["question"]
            hint = payload.get("hint", "")
            content = q + (f"\n\n*{hint}*" if hint else "")
        else:
            content = str(payload)

        append_message("assistant", content)
        _render_chat_message("assistant", content)

        # Update message length snapshot
        msgs = result.get("messages", []) or []
        st.session_state.graph_messages_len = len(msgs)

        st.session_state.awaiting_resume = True
        return

    # No interrupt: add only NEW messages from graph state (diff by length)
    msgs = result.get("messages", []) or []
    start = int(st.session_state.graph_messages_len or 0)
    for msg in msgs[start:]:
        if msg.get("role") == "assistant":
            append_message("assistant", msg.get("content", ""))
            _render_chat_message("assistant", msg.get("content", ""))
    st.session_state.graph_messages_len = len(msgs)

    st.session_state.awaiting_resume = False


if __name__ == "__main__":
    main()
