FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/app

WORKDIR /app

# Runtime dependencies only: requirements.txt without the test-only packages
# (pytest and the packages it pulls in), plus the PostgreSQL driver pinned in
# requirements-postgres.txt. Neither shared requirements file is modified for this.
COPY requirements.txt requirements-postgres.txt ./
RUN grep -viE '^(pytest|iniconfig|pluggy|pygments)==' requirements.txt > /tmp/runtime.txt \
    && grep '^psycopg2-binary==' requirements-postgres.txt >> /tmp/runtime.txt \
    && pip install -r /tmp/runtime.txt \
    && pip check \
    && rm /tmp/runtime.txt

RUN useradd --uid 10001 --user-group --create-home --shell /usr/sbin/nologin app

# Application code stays root-owned, so the unprivileged runtime user cannot modify it.
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY experiments ./experiments
COPY sample_data ./sample_data
# The topology component reads declared manifests from deploy/test-workloads (relative to /app).
COPY deploy ./deploy
COPY --chmod=0755 scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

# Starts as root only to hand the kubeconfig to UID 10001, then drops privileges.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
