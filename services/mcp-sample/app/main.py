# services/mcp-sample/app/main.py
from datetime import datetime
from typing import Optional
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.requests import Request

mcp = FastMCP("Sample Demo MCP")

@mcp.tool()
def ping() -> str:
    return "pong"

@mcp.tool()
def echo(message: str) -> str:
    return message

@mcp.tool()
def now(tz: Optional[str] = "UTC") -> str:
    return f"{datetime.utcnow().isoformat()}Z (tz={tz})"

# Build the MCP Starlette app
mcp_app = mcp.http_app(path="/")  # Streamable HTTP transport as ASGI app

# Wrapper app: accept BOTH /mcp and /mcp/ without redirects, and forward lifespan
app = Starlette(lifespan=mcp_app.lifespan)

# Mount under both paths to avoid 307 redirects in various clients
app.mount("/mcp", mcp_app)   # no trailing slash
app.mount("/mcp/", mcp_app)  # with trailing slash

# Health endpoint on the wrapper
@app.route("/health")
async def health(_request: Request):
    return JSONResponse({"status": "ok"})
