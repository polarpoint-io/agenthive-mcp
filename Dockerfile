# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install project (dependencies + package itself) in one shot
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --upgrade pip \
    && pip install .

# Drop privileges
RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app
USER app

# stdio is the default MCP transport; override with TRANSPORT=http to expose SSE
ENV TRANSPORT=stdio \
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8000

EXPOSE 8000

ENTRYPOINT ["agenthive-mcp"]
