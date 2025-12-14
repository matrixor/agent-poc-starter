FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps (safe default for wheels that may need compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python deps first for better caching
COPY pyproject.toml README.md ./
COPY tsg_officer ./tsg_officer
RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir ".[openai]"

# Copy the rest of the code
COPY . .

EXPOSE 8501

# Streamlit UI
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
