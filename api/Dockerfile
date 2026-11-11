# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.13 AS uv

FROM python:3.12-slim AS build
# lxml and xmlsec (SAML) are built from source against the system libxml2 (pyproject.toml).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential pkg-config libxml2-dev libxslt1-dev libxmlsec1-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="megooflags-api" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="https://github.com/mmhy2003/MegooFlags" \
      org.opencontainers.image.licenses="Apache-2.0"
# The shared libraries lxml and xmlsec were built against (libxml2 comes with them).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxslt1.1 libxmlsec1t64-openssl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --no-create-home megooflags
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY log_config.json ./
# Uvicorn reads FORWARDED_ALLOW_IPS for --proxy-headers: trust only the private (Docker) networks.
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1 \
    FORWARDED_ALLOW_IPS="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1"
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
# uvicorn with MEGOOFLAGS_API_WORKERS workers (default: one per CPU, at most 8); see megooflags_api/serve.py.
CMD ["python", "-m", "megooflags_api.serve"]
