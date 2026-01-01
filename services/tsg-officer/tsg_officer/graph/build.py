from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from langgraph.graph import StateGraph, START, END

from tsg_officer.config import Settings
from tsg_officer.state.models import TSGState
from tsg_officer.tools.llm import (
    LLMClient,
    MockLLMClient,
    OpenAIChatLLMClient,
    OpenAIResponsesLLMClient,
    ChubbGPTLLMClient,
)
from tsg_officer.tools.rules import YamlRuleRepository
from tsg_officer.graph.nodes import (
    route,
    make_intake_node,
    make_checklist_node,
    make_followup_node,
    make_diagram_node,
    make_review_node,
    finalize,
)


def build_dependencies(settings: Settings) -> Tuple[LLMClient, YamlRuleRepository]:
    # LLM
    if settings.llm_provider == "openai":
        # Prefer the new OpenAI responses client for reasoning summaries.
        # Fallback to ChatOpenAI if responses API is not available.
        try:
            llm = OpenAIResponsesLLMClient(model=settings.openai_model)  # type: ignore[assignment]
        except Exception:
            # For backwards compatibility, fall back to ChatOpenAI wrapper
            llm = OpenAIChatLLMClient(model=settings.openai_model)  # type: ignore[assignment]
    elif settings.llm_provider == "chubbgpt":
        llm = ChubbGPTLLMClient(
            model=settings.chubbgpt_model or settings.openai_model,
            checklist_model=settings.chubbgpt_checklist_model or settings.chubbgpt_model or settings.openai_model,
            reasoning_model=settings.chubbgpt_reasoning_model or settings.chubbgpt_model or settings.openai_model,
            proxy_url=settings.chubbgpt_proxy_url,
            auth_url=settings.chubbgpt_auth_url,
            api_version=settings.chubbgpt_api_version,
            app_id=settings.chubbgpt_app_id,
            app_key=settings.chubbgpt_app_key,
            resource=settings.chubbgpt_resource,
        )  # type: ignore[assignment]
    else:
        llm = MockLLMClient()

    # Rules repository
    default_rules_path = Path(__file__).resolve().parent.parent.parent / "data" / "rules" / "rules.v1.yaml"

    if getattr(settings, "rules_path", ""):
        override = Path(str(settings.rules_path)).expanduser()
        # If a relative path is provided, resolve it relative to the package root
        # (which is /app in the docker-compose setup).
        rules_path = override if override.is_absolute() else (default_rules_path.parent.parent.parent / override)
    else:
        rules_path = default_rules_path
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
    builder.add_node("followup", make_followup_node(llm))
    builder.add_node("diagram", make_diagram_node(llm))
    builder.add_node("review", make_review_node(llm))
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
    """Create a SQLite checkpointer.

    We use SQLite so thread/case state is durable across:
      - page refreshes
      - browser sessions
      - Streamlit server restarts

    This enables the "Search session history" UX to actually resume a case by
    Thread / Case ID.

    If SQLite is unavailable for any reason, we fall back to in-memory.
    """

    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        path = Path(str(db_path)).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        # NOTE: check_same_thread=False is OK; SqliteSaver uses an internal lock.
        conn = sqlite3.connect(str(path), check_same_thread=False)
        try:
            # Reduce lock contention for multi-session Streamlit usage.
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            # Non-fatal; continue with defaults.
            pass

        saver = SqliteSaver(conn)
        try:
            saver.setup()
        except Exception:
            # Non-fatal; some versions lazily set up tables.
            pass
        return saver

    except Exception:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
