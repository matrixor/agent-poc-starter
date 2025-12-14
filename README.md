# Agent PoC Starter (Docker + Dev Containers)

This starter lets you build **E2E Agent** PoCs _inside containers_ on Windows 11 with Docker Desktop + VS Code. 
You can spin up isolated stacks for **RAG**, **MCP gateway/registry (placeholder)**, and keep your host machine clean.

## What you get
- **VS Code Dev Container**: code _inside_ a container (Python + Node) with Docker socket passthrough.
- **Compose profiles** to toggle stacks: `rag`, `mcp`, `tsg`.
- Minimal **RAG API** (FastAPI + Qdrant) with `/ingest` and `/ask` endpoints.
- **MCP gateway placeholder** service so you can drop in any real MCP gateway/registry later.
- Shared **Redis** for simple caching/queues if you need them.

> Tip (Windows): Keep the project under **WSL2 Linux filesystem** (e.g. `\wsl$\Ubuntu\home\<you>`) for speed.

---

## 0) Prereqs (one-time on Windows 11 Pro)
1. BIOS: enable virtualization (VT‑x/AMD‑V).
2. Install **WSL2** + Ubuntu (`wsl --install -d Ubuntu`), reboot if prompted.
3. Install **Docker Desktop** → Settings → **Use WSL 2 based engine** + enable integration for Ubuntu.
4. Install **VS Code** + the **Dev Containers** extension.
5. Ensure this folder lives **inside** your WSL2 Linux home, _not_ on `C:`.

---

## 1) Open in Dev Container
- In VS Code: `File → Open Folder…` → pick this folder
- Press **Ctrl+Shift+P** → “**Dev Containers: Reopen in Container**”
- This attaches VS Code to the `dev` container; your editor, terminals, and tools run **inside** the container.

The **Docker socket** is mounted so you can bring up sibling containers from the terminal _inside_ the dev container.

---

## 2) Configure secrets
Copy `.env.example` to `.env` and fill in values:
```bash
cp .env.example .env
```

Required for RAG:
- `OPENAI_API_KEY` — for embeddings + chat completions
- Optionally adjust `EMBEDDING_MODEL`/`CHAT_MODEL`

---

## 3) Start services with profiles

From the **VS Code terminal inside the dev container**:

### Base (Redis only)
```bash
docker compose up -d redis
```

### RAG stack
```bash
docker compose --profile rag up -d
# api: http://localhost:8000/docs
# qdrant: http://localhost:6333 (REST)
```

### MCP stack (placeholder)
```bash
docker compose --profile mcp up -d
# placeholder TCP listener on :6000 (replace with your real MCP gateway/registry)
```


### TSG Officer UI (LangGraph + Streamlit)
```bash
docker compose --profile tsg up -d --build tsg-officer
# ui: http://localhost:8501
```

Env toggles (optional):
- `TSG_LLM_PROVIDER` = `mock` (default) or `openai`
- `TSG_OPENAI_MODEL` = e.g. `gpt-4o-mini`

Stop a stack:
```bash
docker compose --profile rag down
docker compose --profile mcp down
docker compose --profile tsg down
```

See logs:
```bash
docker compose logs -f api
docker compose logs -f mcp-gateway
docker compose logs -f tsg-officer
```

---

## 4) Try the RAG API
1) Put some `.txt` files in `data/docs/` (already has `sample.txt`).  
2) Ingest them:
```bash
curl -X POST http://localhost:8000/ingest
```
3) Ask:
```bash
curl -X POST http://localhost:8000/ask -H "Content-Type: application/json"   -d '{"query":"What is this project and how do I run it?"}'
```

You can also open Swagger UI at **http://localhost:8000/docs**.

---

## 5) Swap in a real MCP gateway/registry
The `mcp-gateway` service is a **placeholder**. To test a real gateway/registry:
1. Replace `services/mcp-gateway/` contents with your gateway code (or mount a volume).
2. Update its `Dockerfile`/`command` accordingly.
3. Rebuild: `docker compose --profile mcp up -d --build`.

---

## 6) Folder layout

```
agent-poc-starter/
├─ .devcontainer/
│  ├─ devcontainer.json
│  └─ Dockerfile
├─ services/
│  ├─ api/
│  │  ├─ Dockerfile
│  │  ├─ requirements.txt
│  │  └─ app/
│  │     └─ main.py
│  ├─ mcp-gateway/
│  │  ├─ Dockerfile
│  │  └─ server.js        # placeholder
│  └─ tsg-officer/
│     ├─ Dockerfile
│     ├─ app/
│     │  └─ streamlit_app.py
│     └─ tsg_officer/...
├─ data/
│  ├─ docs/
│  │  └─ sample.txt
│  └─ tsg-officer/
├─ .env
├─ docker-compose.yml
└─ Makefile
```

---

## 7) Notes & tips
- **Keep containers stateless**: your code is mounted as a volume; images stay disposable.
- **Add stacks** with new `compose` services + profiles (e.g., `agui` for your Agent UI).
- If you need Docker CLIs inside the dev container, they’re already installed. The socket is mounted.
- To use GPU locally (optional), enable GPU sharing in Docker Desktop and add the `deploy.resources.reservations.devices` stanza to services that need it.

---

## 8) Security
- `.env` is **not** committed by default. Rotate keys regularly.
- For enterprise work, add secret stores (Docker/Swarm/HashiCorp Vault/Env files in CI) instead of plain envs.

Happy hacking!
