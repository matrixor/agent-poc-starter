# Hotfix: Avoid empty Authorization header

This patch updates `services/crewai-app/app/crew.py` so it only sets the `Authorization`
header when `MCP_BEARER` (JWT) or `MCP_BASIC` (Base64) is **non-empty**. This prevents
`httpx.LocalProtocolError: Illegal header value b'Bearer '` when the env var isn't set.

## Usage
- For JWT:
  - `export MCP_BEARER=<your_token_without_newlines>`
- For Basic:
  - `export MCP_BASIC=$(printf 'admin:pass' | base64 | tr -d '\n')`

Then:
```bash
docker compose -f docker-compose.yml -f docker-compose.mcp.yml -f docker-compose.crewai.yml   --profile mcp --profile rag --profile crewai up -d crewai-app
```
