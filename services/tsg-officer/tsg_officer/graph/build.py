from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from langgraph.graph import StateGraph, START, END

from tsg_officer.config import Settings
from tsg_officer.state.models import TSGState
from tsg_officer.tools.llm import LLMClient, MockLLMClient, OpenAIChatLLMClient
from tsg_officer.tools.rules import YamlRuleRepository
from tsg_officer.graph.nodes import (
    route,
    make_intake_node,
    make_checklist_node,
    followup,
    make_diagram_node,
    review,
    finalize,
)


def build_dependencies(settings: Settings) -> Tuple[LLMClient, YamlRuleRepository]:
    # LLM
    if settings.llm_provider == "openai":
        llm: LLMClient = OpenAIChatLLMClient(model=settings.openai_model)
    else:
        llm = MockLLMClient()

    # Rules repository
    rules_path = Path(__file__).resolve().parent.parent.parent / "data" / "rules" / "sample_rules.yaml"
    rules_repo = YamlRuleRepository(rules_path)

    return llm, rules_repo


def build_graph(*, settings: Optional[Settings] = None):
    settings = settings or Settings.from_env()
    llm, rules_repo = build_dependencies(settings)

    builder = StateGraph(TSGState)

    # Nodes
    builder.add_node("route", route)
    builder.add_node("intake", make_intake_node(llm))
    builder.add_node("checklist", make_checklist_node(llm, rules_repo))
    builder.add_node("followup", followup)
    builder.add_node("diagram", make_diagram_node(llm))
    builder.add_node("review", review)
    builder.add_node("finalize", finalize)

    # Entry
    builder.add_edge(START, "route")

    # finalize ends the run
    builder.add_edge("finalize", END)

    # Checkpointing (SQLite) for durable threads + interrupts
    checkpointer = _build_checkpointer(settings.checkpoint_db)

    graph = builder.compile(checkpointer=checkpointer)
    return graph


def _build_checkpointer(db_path: str):
    """Create a SQLite checkpointer. Falls back to in-memory if unavailable."""
    # Temporarily use MemorySaver to avoid database lock issues with multiple instances
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()
