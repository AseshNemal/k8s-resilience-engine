# syntax=docker/dockerfile:1
# Docker Desktop extension image. Build from the repository root:
#   docker build -f extension/Dockerfile -t k8s-resilience-engine:0.1.0 .

# --- Backend runtime: the unmodified application plus the extension wrapper -----------------
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/app
WORKDIR /app

# Runtime dependencies only: requirements.txt without test-only packages, plus the PostgreSQL
# driver pinned in requirements-postgres.txt (the application still supports PostgreSQL).
COPY requirements.txt requirements-postgres.txt ./
RUN grep -viE '^(pytest|iniconfig|pluggy|pygments)==' requirements.txt > /tmp/runtime.txt \
    && grep '^psycopg2-binary==' requirements-postgres.txt >> /tmp/runtime.txt \
    && pip install -r /tmp/runtime.txt \
    && pip check \
    && rm /tmp/runtime.txt

RUN useradd --uid 10001 --user-group --create-home --shell /usr/sbin/nologin app

# Code stays root-owned so the unprivileged runtime user cannot modify it.
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY experiments ./experiments
COPY sample_data ./sample_data
COPY deploy ./deploy
COPY extension/__init__.py extension/asgi.py extension/cluster_connection.py extension/error_envelope.py extension/healthcheck.py ./extension/

# --- Render the existing dashboard pages to static files (build time only) ------------------
FROM runtime AS render
COPY extension/build_ui.py ./extension/build_ui.py
RUN python -m extension.build_ui /out

# --- Final extension image ------------------------------------------------------------------
FROM runtime
LABEL org.opencontainers.image.title="K8s Resilience Engine" \
      org.opencontainers.image.description="Dependency-aware resilience assessment for Kubernetes deployments, inside Docker Desktop. Read-only cluster access; nothing is applied automatically." \
      org.opencontainers.image.vendor="K8s Resilience Engine research project (SLIIT J26-SE-311)" \
      com.docker.desktop.extension.api.version=">= 0.2.3" \
      com.docker.extension.detailed-description="Reads a local Kubernetes test cluster with a read-only, time-limited credential and shows which workflows survive worker-node failures. Recommendations are never applied automatically." \
      com.docker.extension.changelog="Initial local build. Not published; the Marketplace-only labels (icon URL, screenshots, publisher URL) are intentionally omitted." \
      com.docker.extension.categories="kubernetes,utility-tools"

COPY extension/metadata.json extension/compose.yaml extension/icon.svg /
COPY --from=render /out /ui
COPY extension/ui-src/shim.js extension/ui-src/chrome.js extension/ui-src/connect.js extension/ui-src/docker.css /ui/
COPY --chmod=0755 extension/entrypoint.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
