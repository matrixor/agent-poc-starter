# services/mcp-fin-analyst/app/main.py
from typing import Optional
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.requests import Request
from fastmcp import FastMCP
from .finance_crew import run_financial_analysis

mcp = FastMCP("financial-analyst")

@mcp.tool
def analyze_stock(query: str) -> str:
    # 输入自然语言，例如：'绘制 AAPL 近 6 个月走势'。
    # 返回图片相对路径，例如：'app/out/output.png'。
    return run_financial_analysis(query)

# Build the MCP Starlette app (Streamable HTTP transport as ASGI app)
mcp_app = mcp.http_app(path="/")

# Wrapper app: accept both /mcp and /mcp/; include /health for Docker healthcheck
app = Starlette(lifespan=mcp_app.lifespan)
app.mount("/mcp", mcp_app)
app.mount("/mcp/", mcp_app)

@app.route("/health")
async def health(_request: Request):
    return JSONResponse({"status": "ok"})
