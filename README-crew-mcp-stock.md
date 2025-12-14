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


=============================
下面给你一套**从网关 /rpc 直连测试**到拿到图片的最小流程（按行复制即可）：

```bash
# 0) 生成 Bearer Token（直接在网关容器里用相同 SECRET）
TOKEN=$(docker exec mcpgateway sh -lc \
'python3 -m mcpgateway.utils.create_jwt_token --username ${MCP_ADMIN_USER:-admin} --exp 10080 --secret "$JWT_SECRET_KEY"')
```

```bash
# 1) 初始化 MCP 会话（推荐，但一次性简单测试也能直接调工具）
curl -s -X POST http://localhost:4444/rpc \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
        "protocolVersion":"2025-03-26",
        "capabilities":{"tools":{},"resources":{},"prompts":{}},
        "clientInfo":{"name":"curl","version":"1.0"}}}' | jq
# （可选）通知 initialized
curl -s -X POST http://localhost:4444/rpc \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' | jq
```

> 上面这两步是标准 MCP 初始化序列：`initialize` → `notifications/initialized`。([IBM GitHub][1])

```bash
# 2) 看看工具是否已注册（可选）
curl -s -X POST http://localhost:4444/rpc \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | jq
# 预期能看到 name: "analyze_stock"
```

> `tools/list` 是列举工具的标准方法。([IBM GitHub][1])

```bash
# 3) 调用工具（自然语言入参）
curl -s -X POST http://localhost:4444/rpc \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{
        "name":"analyze_stock_analyze_stock",
        "arguments":{"query":"绘制 AAPL 近 6 个月走势"}
      }}' | jq
```

We should show expected response shape and result. Then copy the image:

```bash
docker cp mcp-fin-analyst:/app/output.png ./output.png
```

Add alt queries, e.g., TSLA 1y. Provide quick health check for mcp-fin-analyst.

Ensure we include citations for "tools/call". We'll cite turn3view1.

Let's finalize properly.

[1]: https://ibm.github.io/mcp-context-forge/development/mcp-developer-guide-json-rpc/ "MCP JSON RPC Guide - MCP Context Forge - Model Context Protocol Gateway"
