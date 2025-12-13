# CrewAI + MCP Add-on (Compose Profile: `crewai`)

This add-on plugs into your existing `agent-poc-starter` stack and introduces a new Compose profile `crewai` that
runs a minimal CrewAI app wired to an MCP server via **Streamable HTTP**.

## Files
- `docker-compose.crewai.yml` — adds the `crewai-app` service (profile: `crewai`)
- `services/crewai-app/` — Dockerized CrewAI demo that connects to `mcp-sample` (`http://mcp-sample:8000/mcp`)
- `MAKEFILE_APPEND.crewai` — Makefile targets you can paste into your repo's `Makefile`

## Bring it up
```bash
# From your repo folder (where docker-compose.yml exists):
docker compose -f docker-compose.yml -f docker-compose.mcp.yml -f docker-compose.crewai.yml --profile mcp --profile crewai up -d

# Watch CrewAI task output
docker compose -f docker-compose.yml -f docker-compose.mcp.yml -f docker-compose.crewai.yml logs -f crewai-app
```

The CrewAI app will:
1) Discover MCP tools from `mcp-sample`,
2) Invoke `now` and `echo`,
3) Print a JSON-like result.

## Switching to the Gateway URL (optional)
If you register `mcp-sample` inside **Context Forge Gateway** and expose it as a virtual server with Streamable HTTP,
you can point the CrewAI app to the gateway instead:

```bash
# Example: use gateway host + virtual server route (adjust to your config)
export MCP_URL=http://mcpgateway:4444/mcp
docker compose -f docker-compose.yml -f docker-compose.mcp.yml -f docker-compose.crewai.yml --profile mcp --profile crewai up -d crewai-app
```

## Use Ollama as the LLM
By default we talk to Ollama's OpenAI-compatible endpoint at `http://ollama:11434/v1` with a dummy key.
Change model via env: `OPENAI_MODEL=llama3.2:3b` (ensure it's pulled in your `ollama` container).

---

**Security note:** This is for local PoC only. If you expose ports beyond localhost, enable auth on the gateway and secure network paths.
