FROM python:3.11-slim

ENV PIP_NO_CACHE_DIR=1     PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

# System deps (curl useful for quick health checks)
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# App
COPY app /app/app

# Default envs for local Ollama OpenAI-compatible endpoint
ENV OPENAI_API_KEY="ollama"     OPENAI_BASE_URL="http://ollama:11434/v1"     OPENAI_MODEL="llama3.2:3b"     MCP_URL="http://mcp-sample:8000/mcp"

# Run a single demo task on startup (can override in compose)
ENTRYPOINT ["python", "-m", "app.crew"]
