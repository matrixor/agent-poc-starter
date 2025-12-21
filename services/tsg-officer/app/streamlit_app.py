from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # loads .env into os.environ


import json
import uuid
from typing import Any, Dict, List

import streamlit as st

from langgraph.types import Command

from tsg_officer.config import Settings
from tsg_officer.graph import build_graph
from tsg_officer.state import new_case_state


st.set_page_config(page_title="TSG Officer", page_icon="✅", layout="wide")


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
    st.sidebar.title("TSG Officer")
    st.sidebar.caption("LangGraph + Streamlit scaffold")

    st.sidebar.write("**Thread / Case ID**")
    st.sidebar.code(st.session_state.thread_id)

    if st.sidebar.button("New Case"):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.awaiting_resume = False
        st.session_state.chat = []
        st.session_state.initialized = False
        st.rerun()

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
            # resume the graph with the submission text as the user's reply.  This
            # mirrors the behaviour when the user replies in the chat UI.  The
            # assistant's follow‑up messages (if any) are appended to the chat and
            # the awaiting_resume flag is updated accordingly.
            if st.session_state.get("awaiting_resume", False):
                try:
                    # Provide the submission text as the user's answer to resume the interrupt.
                    result = graph.invoke(
                        Command(
                            resume=submission_text,
                            update={"messages": [{"role": "user", "content": submission_text}]},
                        ),
                        config=config,
                    )

                    # Append any new assistant messages from the graph state to the chat.
                    msgs: List[Dict[str, Any]] = result.get("messages", []) or []
                    # Only append messages beyond the last seen index to avoid duplicates.
                    start_idx = int(st.session_state.get("graph_messages_len", 0) or 0)
                    for msg in msgs[start_idx:]:
                        if msg.get("role") == "assistant":
                            # record in local chat history
                            st.session_state.chat.append({"role": "assistant", "content": msg.get("content", "")})
                    # Update the graph message length snapshot
                    st.session_state.graph_messages_len = len(msgs)

                    # If the result contains another interrupt, append its question to the chat
                    # and mark awaiting_resume = True.  Otherwise, we're ready for the next user turn.
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
                        # No interrupt means we've progressed; clear awaiting_resume.
                        st.session_state.awaiting_resume = False
                except Exception:
                    # If invoking fails (e.g. due to invalid API key), ignore and still show success message.
                    st.session_state.awaiting_resume = False

            st.sidebar.success("Attached to case state (intake.submission_text).")
            # Rerun the app so that the new state is reflected in the UI.
            st.rerun()

    st.sidebar.divider()
    # Hidden by default; enable with URL param like ?debug=1
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

                # Audit log
                st.write("**Audit log**")
                st.json(snap.values.get("audit_log", []))

                # Display any captured reasoning summaries (classification/checklist/flowchart)
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

    st.sidebar.divider()
    st.sidebar.caption("Tip: run with mock LLM by default. Set env TSG_LLM_PROVIDER=openai for real LLM.")


def render_chat():
    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])


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
    sidebar(graph)

    st.title("✅ TSG Officer")
    st.caption("Chat-first approval assistant (LangGraph interrupts + auditable checklist JSON)")

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
    with st.chat_message("user"):
        st.markdown(user_text)

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
        with st.chat_message("assistant"):
            st.markdown(content)

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
            with st.chat_message("assistant"):
                st.markdown(msg.get("content", ""))
    st.session_state.graph_messages_len = len(msgs)

    st.session_state.awaiting_resume = False


if __name__ == "__main__":
    main()
