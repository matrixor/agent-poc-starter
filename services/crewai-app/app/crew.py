import os
from rich import print
from crewai import Agent, Task, Crew, Process, LLM
from crewai_tools import MCPServerAdapter

# LLM config (pointed to Ollama's OpenAI-compatible endpoint by default)
llm = LLM(
    model=os.getenv("OPENAI_MODEL", "ollama/llama3.2:3b"),
    api_key=os.getenv("OPENAI_API_KEY", "ollama"),
    base_url=os.getenv("OPENAI_BASE_URL", "http://ollama:11434/v1"),
)

#MCP_URL = os.getenv("MCP_URL", "http://mcp-sample:8000/mcp")
MCP_URL = os.getenv("MCP_URL", "http://mcpgateway:4444/mcp")

def run_once():
    # Build auth headers only if present
    headers = {}
    bearer = os.getenv("MCP_BEARER", "").strip()
    basic = os.getenv("MCP_BASIC", "").strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    elif basic:
        headers["Authorization"] = f"Basic {basic}"
    print("[yellow]MCP auth header is set[/yellow]")

    if headers:
        scheme = "Bearer" if bearer else "Basic"
        value = bearer if bearer else basic
        print("[red]:warning: Printing FULL token because MCP_DEBUG_SHOW_TOKENS is enabled. Disable it in production![/red]")
        print(f"[cyan]Authorization:[/cyan] {scheme} {value}")
    else:
        print("[yellow]MCP auth header NOT set (continuing without auth)[/yellow]")

    # Connect via Streamable HTTP to the MCP server/gateway
    server_params = {
        "url": MCP_URL,
        "transport": "streamable-http",
        "headers": headers or None,
    }

    print(f"[green]Connecting to MCP:[/green] {MCP_URL}")
    with MCPServerAdapter(server_params, connect_timeout=60) as mcp_tools:
        print(f"[bold green]MCP tools discovered:[/bold green] {[t.name for t in mcp_tools]}")

        # Agent that can use the MCP tools
        agent = Agent(
            role="Tool User",
            goal="Use MCP tools to fetch the current time and echo a message.",
            backstory="Designed for validating MCP connectivity inside Docker.",
            tools=mcp_tools,
            llm=llm,
            verbose=True,
        )

        task = Task(
            description=(
                "1) Call the 'now' tool to get an ISO8601 timestamp. "
                "2) Call the 'echo' tool with message='CrewAI+MCP OK'. "
                "Return both results."
            ),
            agent=agent,
            expected_output="A short JSON object containing now and echo outputs."
        )

        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential)
        result = crew.kickoff()
        print("\n[bold cyan]MCP_BEARER:[/bold cyan]\n", os.getenv('MCP_BEARER',''))
        print("\n[bold cyan]Result:[/bold cyan]\n", result)

if __name__ == "__main__":
    run_once()
