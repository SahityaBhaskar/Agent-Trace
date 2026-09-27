# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install dependencies first (layer-cached separately from source)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY agent_trace/ ./agent_trace/
COPY pyproject.toml .

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM builder AS runtime

# Expose the HTTP API port
EXPOSE 8000

# Bind to all interfaces inside container (mapped by docker-compose/k8s)
ENV AGENTTRACE_HOST=0.0.0.0

# Knowledge graph persists in a named volume mapped to this path
VOLUME ["/root/.agenttrace"]

CMD ["python", "-m", "agent_trace.cli", "serve", "--port", "8000"]
