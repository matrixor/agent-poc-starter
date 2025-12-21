# TSG AI Officer (LangGraph + Streamlit)

This repo is for building a chat-first “virtual role” — a **TSG AI Officer** that guides an applicant through intake and produces a **consistent, auditable checklist report**.

It is intentionally **UI-first (Streamlit)** and **workflow-first (LangGraph)**:
- Streamlit provides a **ChatGPT-like** interface using `st.chat_message` + `st.chat_input`.
- LangGraph provides the **state machine + human-in-the-loop** workflow (pause/resume with interrupts).

---

## Quickstart

### 1) Create a venv and install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2) Run the app

```bash
streamlit run app/streamlit_app.py
```

By default this runs with a **Mock LLM** (so you can test the flow without any API keys).

---

## Using a real LLM (optional)

This scaffold includes an LLM interface layer so you can swap providers.

### Option A: OpenAI via LangChain (example)

```bash
pip install -e ".[openai]"
export OPENAI_API_KEY="..."
export TSG_LLM_PROVIDER="openai"
export TSG_OPENAI_MODEL="gpt-4o-mini"   # or your org-approved model name
```

> You can also implement your own `LLMClient` in `tsg_officer/tools/llm.py`.

---

## What’s inside

```
app/streamlit_app.py              # ChatGPT-like UI
tsg_officer/graph/                # LangGraph build + nodes
tsg_officer/state/                # State typing + helpers
tsg_officer/tools/                # Interfaces (LLM, rules repo, docs, audit)
tsg_officer/prompts/              # System prompt(s)
tsg_officer/schemas/              # JSON Schemas (auditable outputs)
data/rules/sample_rules.yaml      # Example rule library
tests/                            # Schema + smoke tests
```

---

## Regenerate JSON Schemas

Schemas are defined as Pydantic models in `tsg_officer/schemas/models.py`.

```bash
python scripts/export_schemas.py
```

---

## Notes / next steps

This scaffold is intentionally “thin but real”:
- ✅ End-to-end graph runs
- ✅ Interrupt-driven Q&A intake loop
- ✅ Checklist output validated against JSON Schema
- ✅ Audit log captured in state
- ✅ Streamlit UI

To make it production-grade, the biggest upgrades are:
- RAG (vector search) over guidelines + prior cases
- Strong document ingestion (PDFs, drawings, scanned images)
- Role-based access for reviewer approvals
