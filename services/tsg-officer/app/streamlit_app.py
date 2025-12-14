from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

import streamlit as st

from langgraph.types import Command

from tsg_officer.config import Settings
from tsg_officer.graph import build_graph
from tsg_officer.state import new_case_state


st.set_page_config(page_title="TSG Officer", page_icon="✅", layout="wide")


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
        if submission_text.strip():
            config = {"configurable": {"thread_id": st.session_state.thread_id}}
            # Merge into existing intake to avoid overwriting other collected fields
            try:
                snap = graph.get_state(config)
                intake = dict(snap.values.get("intake", {}) or {})
            except Exception:
                intake = {}
            intake["submission_text"] = submission_text
            graph.update_state(config, {"intake": intake})
            st.sidebar.success("Attached to case state (intake.submission_text).")

    st.sidebar.divider()
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
        res = bootstrap_case(graph)
        # The graph writes assistant greeting into state messages; easiest is to just show it here too.
        # We'll also store it in local UI chat for ChatGPT-like display.
        msgs = res.get("messages", [])
        for msg in msgs:
            append_message(msg["role"], msg["content"])
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
