# CrewAI + MCP + Stock (Profile Add-on)

Adds a new **Docker Compose profile** `stock` with an MCP server: **mcp-fin-analyst**.

**Tool exposed:** `analyze_stock(query: str) -> str`  
Returns a PNG path like `app/out/output.png` for queries such as “绘制 AAPL 近 6 个月走势”.

Transport: **FastMCP Streamable HTTP** at `http://mcp-fin-analyst:8000/mcp`.

> ⚠️ PoC note: CodeInterpreterTool runs with `unsafe_mode=True` (no Docker-in-Docker). Do not use in production.

## Usage

1. Copy this add-on into the root of your PoC (next to existing compose files).
2. Start gateway + server:

```bash
docker compose -f docker-compose.yml -f docker-compose.mcp.yml -f docker-compose.stock.override.yml   --profile mcp --profile stock up -d --build
```

3. Register in **MCP Gateway Admin UI** (http://localhost:4444):
   - Servers → **Add MCP Server** → Type **HTTP (Streamable)**.
   - URL: `http://mcp-fin-analyst:8000/mcp`
   - Connect/Activate → verify the tool `analyze_stock` is visible.

   Or quickly list tools via RPC:
```bash
export MCPGATEWAY_BEARER_TOKEN=$(python3 -m mcpgateway.utils.create_jwt_token --username admin --secret my-key)
curl -s -H "Authorization: Bearer $MCPGATEWAY_BEARER_TOKEN" http://localhost:4444/rpc   -H "Content-Type: application/json"   -d '{"jsonrpc":"2.0","id":1,"method":"list_tools"}' | jq
```

4. Invoke from clients behind the gateway (your CrewAI app, Claude Desktop via gateway, etc.).

## Env Vars

- `OPENAI_API_KEY` (**required**)
- `OPENAI_BASE_URL` (optional, defaults to OpenAI API)
- `OPENAI_MODEL` (defaults to `gpt-4o`)

## Security

For production switch CodeInterpreterTool to safe Docker sandbox and ensure Docker is available on the host. Consider network egress controls and read-only volumes.
