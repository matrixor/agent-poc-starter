# ContextForge MCP Gateway — PoC Workshop (Drop‑in for `agent-poc-starter`)

This workshop replaces the placeholder **mcp-gateway** container in `agent-poc-starter` with **ContextForge MCP Gateway** (IBM/mcp-context-forge).

## What you'll build
- A running **MCP Gateway/Registry** at `http://localhost:4444`
- Admin UI enabled (Basic Auth)
- JWT auth ready for API/programmatic access
- SQLite persisted at `./data/mcp/mcp.db`

> Repo & docs: https://github.com/IBM/mcp-context-forge

---

## 0. Prereqs
- Docker Desktop 4.19+ (or Docker Engine) running
- Ports **4444** free on host
- From the project root: `agent-poc-starter/`

---

## 1. Files added by this workshop
- `docker-compose.mcp.yml` — adds `mcpgateway` service using official image `ghcr.io/ibm/mcp-context-forge:0.6.0`
- `.env` — appended `MCP_ADMIN_USER`, `MCP_ADMIN_PASS`, `MCP_JWT_SECRET`
- Data dir: `./data/mcp/` for SQLite volume

> Keep your existing `docker-compose.yml` untouched. We compose‑merge with the override file.

---

## 2. Launch the gateway
```bash
cd agent-poc-starter
docker compose -f docker-compose.yml -f docker-compose.mcp.yml --profile mcp up -d mcpgateway
docker compose ps
```

Wait a few seconds, then browse:
- **Admin UI**: http://localhost:4444/admin  (user: `$MCP_ADMIN_USER`, pass: `$MCP_ADMIN_PASS`)

Logs:
```bash
docker logs -f mcpgateway
```

DB file will appear at `./data/mcp/mcp.db`.

---

## 3. (Optional) Generate a JWT token for API access
```bash
# one-off token generator inside the official image
docker run --rm -e PYTHONUNBUFFERED=1 ghcr.io/ibm/mcp-context-forge:0.6.0   python3 -m mcpgateway.utils.create_jwt_token --username admin --exp 10080 --secret "$MCP_JWT_SECRET"
```

Use the resulting token for requests against the Admin/API.

Example ping:
```bash
curl -s -H "Authorization: Bearer $MCP_BEARER" http://localhost:4444/version | jq
```

---

## 4. Add an MCP server (two easy paths)

### A) Through the Admin UI (no code)
1. Open http://localhost:4444/admin
2. **Servers → New** → choose a protocol (e.g., **SSE** or **streamable-http**)
3. Point it to any MCP server you run (on host or another container)

### B) Quick demo server via translator (stdio → SSE)
If you have an MCP server that only speaks **stdio**, you can expose it over HTTP with the built‑in **translator**.

**Option 1: one‑shot (cli):**
```bash
# Expose the sample Go time server over SSE on :8003
docker run --rm -p 8003:8003 ghcr.io/ibm/mcp-context-forge:0.6.0   python3 -m mcpgateway.translate     --stdio "docker run --rm -i ghcr.io/ibm/fast-time-server:latest -transport=stdio"     --expose-sse     --port 8003
```

Then in the Admin UI, create a server with URL: `http://host.docker.internal:8003/sse`.

> On Linux, if `host.docker.internal` is not available, use your host IP (e.g., `http://127.0.0.1:8003/sse`) and ensure the gateway can reach it.

---

## 5. Wire your MCP client
You can now point any MCP‑capable client to a particular **server endpoint** exposed by the gateway, e.g.:  
`http://localhost:4444/servers/<SERVER_UUID>/mcp`

See docs for wrapper examples and Claude Desktop JSON.


---

## 6. Tear down
```bash
docker compose -f docker-compose.yml -f docker-compose.mcp.yml --profile mcp down
```

---

## Troubleshooting
- Port in use → change `4444:4444` in `docker-compose.mcp.yml`
- Admin 401 → verify `.env` values or reset with new container
- Token issues → ensure the same `MCP_JWT_SECRET` is used for generation & validation

---

## Notes
- ContextForge is **alpha/early beta**; for PoC use only.
- For observability, set `OTEL_ENABLE_OBSERVABILITY=true` and point to Phoenix/Jaeger (4317).

Happy hacking!
