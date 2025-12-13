# Sample MCP Server (Streamable HTTP)

This adds a **remote (HTTP)** MCP server container you can register in MCP Context Forge.

## Build & Run

```bash
docker compose -f docker-compose.mcp.yml --profile mcp build mcp-sample
docker compose -f docker-compose.mcp.yml --profile mcp up -d
```

- MCP Sample health: http://localhost:8081/health  → `{"status":"ok"}`
- MCP Sample Streamable HTTP base: `http://mcp-sample:8000/mcp`

## Register in Context Forge

1. Start the gateway:

```bash
# if not already started
docker compose -f docker-compose.mcp.yml --profile mcp up -d mcpgateway
```

2. Open **Admin UI** at `http://localhost:4444` (user:`${MCP_ADMIN_USER:-admin}`, pass:`${MCP_ADMIN_PASSWORD:-pass}` by default in compose).
3. In the **Servers** tab → **Add Server**:
   - **Type**: *Streamable HTTP*
   - **Name**: `Sample Demo MCP`
   - **Base URL**: `http://mcp-sample:8000/mcp`
   - **Auth**: *(none)*

4. Click **Save**, then use the **Test** button to list tools. You should see:
   - `ping` → returns "pong"
   - `echo` → echoes input
   - `now` → returns ISO8601 timestamp

### Optional: cURL a quick health & version on the gateway

Generate a bearer token (replace secret with the one in `.env` / env var):

```bash
TOKEN=$(docker exec -i mcpgateway python3 -m mcpgateway.utils.create_jwt_token --username admin --exp 10080 --secret "$JWT_SECRET_KEY")
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:4444/health
```

## Notes

- This server uses **FastMCP** and the **Streamable HTTP** transport (recommended).
- If you prefer SSE, you can switch to `mcp.run(transport="sse")` pattern; but Streamable HTTP is the current best practice.
