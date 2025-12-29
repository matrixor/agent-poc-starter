from __future__ import annotations

import uuid

from langgraph.types import Command

from tsg_officer.config import Settings
from tsg_officer.graph import build_graph
from tsg_officer.state import new_case_state


def test_graph_smoke_interrupt_then_resume():
    settings = Settings(llm_provider="mock", checkpoint_db=":memory:", openai_model="gpt-4o-mini")
    graph = build_graph(settings=settings)

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # First invoke should interrupt to ask an intake question
    res = graph.invoke(new_case_state(thread_id), config=config)
    assert "__interrupt__" in res

    # Resume with a value; also append the user's message to graph state for audit
    res2 = graph.invoke(Command(resume="building_permit", update={"messages": [{"role": "user", "content": "building_permit"}]}), config=config)

    # Graph should continue and either ask next intake question (interrupt) or proceed further
    assert isinstance(res2, dict)
